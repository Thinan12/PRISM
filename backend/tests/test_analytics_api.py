"""Phase 3 acceptance at API level: questions return verified results with the exact SQL;
dashboards are generated, filtered, cross-filtered, edited and exported."""

import io
import json

import duckdb
import httpx
import openpyxl
import pytest

from app.blueprint import ai as ai_mod
from app.core.config import get_settings
from tests.fixtures.make_samples import messy_call_log_xlsx

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def env(client, auth, seeded):
    ws = seeded["ws"]
    base = f"/api/v1/workspaces/{ws}"
    data, _ = messy_call_log_xlsx()
    src = client.post(f"{base}/sources", headers=auth["editor"], files={"file": ("analytics.xlsx", data, XLSX)}).json()
    assert src["status"] == "ready"
    calls = max(src["datasets"], key=lambda d: d["row_count"])
    return {"base": base, "src": src, "calls": calls["id"]}


def _ask(client, auth, env, question, who="viewer", engine="auto"):
    r = client.post(f"{env['base']}/datasets/{env['calls']}/questions", headers=auth[who], json={"question": question, "engine": engine})
    assert r.status_code == 202, r.text
    assert r.json()["job"]["status"] == "succeeded", r.json()["job"]
    q = client.get(f"{env['base']}/questions/{r.json()['id']}", headers=auth[who])
    assert q.status_code == 200
    return q.json()


def _rerun_sql(client, auth, env, sql):
    """Independent check: execute the displayed SQL ourselves against the dataset's rows."""
    import pandas as pd

    rows, names, offset = [], [], 0
    while True:
        page = client.get(f"{env['base']}/datasets/{env['calls']}/rows?offset={offset}&limit=1000", headers=auth["viewer"]).json()
        rows += page["rows"]
        names = page["columns"]
        if len(page["rows"]) < 1000:
            break
        offset += 1000
    detail = client.get(f"{env['base']}/datasets/{env['calls']}", headers=auth["viewer"]).json()
    df = pd.DataFrame(rows, columns=names)
    for c in detail["columns"]:
        if c["logical_type"] in ("date", "datetime") and c["key"] in df:
            df[c["key"]] = pd.to_datetime(df[c["key"]])
    con = duckdb.connect()
    con.register("data", df)
    return con.execute(sql).fetchall()


def test_question_answered_with_verifiable_sql(client, auth, env):
    q = _ask(client, auth, env, "Total call cost by branch")
    assert q["status"] == "answered" and q["engine"] == "rules" and q["chart"] == "bar"
    res = q["result"]
    assert res["sql"].startswith('SELECT "branch" AS d0, SUM(') and res["dataset_version"] == 1
    # the SQL shown to the user reproduces the returned numbers when run independently
    again = _rerun_sql(client, auth, env, res["sql"])
    assert [list(r) for r in again] == [r[:2] for r in res["rows"]]
    top = res["rows"][0]
    assert top[0] in q["answer"] and f"{int(top[1]):,} ₭" in q["answer"]  # answer text is built from the result
    assert "not configured" in q["fallback_reason"] or "switched off" in q["fallback_reason"]


def test_lao_question_with_value_filter(client, auth, env):
    q = _ask(client, auth, env, "ສາຂາໃດມີການຮ້ອງຮຽນຫຼາຍທີ່ສຸດ")
    assert q["status"] == "answered"
    spec = q["spec"]
    assert spec["filters"] == [{"column": "type", "op": "eq", "values": ["ຮ້ອງຮຽນ"]}]
    assert spec["sort"]["direction"] == "desc"
    assert "'ຮ້ອງຮຽນ'" in q["result"]["sql"]
    again = _rerun_sql(client, auth, env, q["result"]["sql"])
    assert [list(r) for r in again] == q["result"]["rows"]


def test_unanswerable_question_is_honest(client, auth, env):
    q = _ask(client, auth, env, "What will the weather be tomorrow?")
    assert q["status"] == "unanswerable" and q["result"] is None and "could not relate" in q["answer"]


def test_question_history_and_isolation(client, auth, env, seeded):
    hist = client.get(f"{env['base']}/datasets/{env['calls']}/questions", headers=auth["viewer"]).json()
    assert len(hist) >= 3 and hist[0]["result"] is None  # history is light; details via /questions/{id}
    other = f"/api/v1/workspaces/{seeded['other_ws']}"
    assert client.get(f"{other}/questions/{hist[0]['id']}", headers=auth["outsider"]).status_code == 404
    assert client.post(f"{other}/datasets/{env['calls']}/questions", headers=auth["outsider"],
                       json={"question": "how many calls"}).status_code == 404


def test_ai_question_retries_on_invalid_query(client, auth, env, monkeypatch):
    """The model first names a column that doesn't exist; PRISM feeds the error back; the second answer is used."""
    monkeypatch.setattr(get_settings(), "ai_provider", "ollama")
    bad = {"answerable": True, "reason": "", "measures": [{"aggregation": "sum", "column": "revenue", "filter_column": None, "filter_values": []}],
           "dimensions": [], "filters": [], "sort_direction": None, "limit": None, "analysis": "none", "chart": "kpi_card"}
    good = dict(bad, measures=[{"aggregation": "sum", "column": "col_8", "filter_column": "type", "filter_values": ["ຮ້ອງຮຽນ"]}])
    replies = [bad, good]
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        body = replies.pop(0)
        return httpx.Response(200, json={"model": "qwen2.5:7b", "message": {"content": json.dumps(body, ensure_ascii=False)},
                                         "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 10})

    monkeypatch.setattr(ai_mod, "http_factory", lambda t: httpx.Client(transport=httpx.MockTransport(handler)))
    q = _ask(client, auth, env, "How much did complaints cost?", engine="ai")
    assert q["status"] == "answered" and q["engine"] == "ai" and q["model"] == "ollama/qwen2.5:7b"
    assert "unknown measure column 'revenue'" in seen[1]["messages"][-1]["content"]
    assert 'FILTER (WHERE CAST("type" AS VARCHAR) = \'ຮ້ອງຮຽນ\')' in q["result"]["sql"]
    payload = json.loads(seen[0]["messages"][1]["content"].split("\n", 1)[1])
    assert "rows" not in json.dumps(payload["dataset"]["columns"])  # schema + values list only
    assert any(c["key"] == "type" and "ຮ້ອງຮຽນ" in c["values"] for c in payload["dataset"]["columns"])
    assert seen[0]["format"]["properties"]["measures"]["maxItems"] == 4


def test_ai_question_failure_falls_back_to_rules(client, auth, env, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_provider", "ollama")

    def down(req):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(ai_mod, "http_factory", lambda t: httpx.Client(transport=httpx.MockTransport(down)))
    q = _ask(client, auth, env, "How many calls?", engine="ai")
    assert q["status"] == "answered" and q["engine"] == "rules" and "Could not reach Ollama" in q["fallback_reason"]
    assert q["result"]["rows"] == [[61]]


def test_query_endpoint_and_validation(client, auth, env):
    url = f"{env['base']}/datasets/{env['calls']}/query"
    spec = {"dataset_id": env["calls"], "measures": [{"aggregation": "count_rows"}], "dimensions": [{"column": "date", "grain": "month"}],
            "analysis": "change"}
    r = client.post(url, headers=auth["viewer"], json=spec)
    assert r.status_code == 200 and [row[1] for row in r.json()["rows"]] == [19, 18, 24]
    bad = client.post(url, headers=auth["viewer"], json={**spec, "dimensions": [{"column": "branch", "grain": "month"}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_query"
    assert client.post(url, headers=auth["viewer"], json={**spec, "sql": "DROP TABLE x"}).status_code == 422
    assert client.post(url, headers=auth["viewer"], json={**spec, "dataset_id": 999999}).status_code == 422


def test_insights_have_sql(client, auth, env):
    ins = client.get(f"{env['base']}/datasets/{env['calls']}/insights", headers=auth["viewer"]).json()
    kinds = {i["kind"] for i in ins}
    assert {"trend", "ranking", "share", "anomaly"} <= kinds
    assert all(i["sql"].startswith(("SELECT", "WITH")) and i["text"] for i in ins)


def test_dashboard_lifecycle(client, auth, env):
    base, src = env["base"], env["src"]
    assert client.post(f"{base}/sources/{src['id']}/dashboards", headers=auth["viewer"], json={}).status_code == 403
    r = client.post(f"{base}/sources/{src['id']}/dashboards", headers=auth["editor"], json={})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["origin"] == "rules" and d["revision"] == 1
    charts = [p["chart"] for p in d["content"]["panels"]]
    assert charts.count("kpi_card") >= 2 and "bar" in charts and "line" in charts
    assert any(p["query"]["analysis"] == "anomaly" for p in d["content"]["panels"])
    assert {f["column"] for f in d["content"]["filters"]} >= {"date", "branch"}

    # compute all panels
    data = client.post(f"{base}/dashboards/{d['id']}/data", headers=auth["viewer"], json={}).json()
    assert len(data["panels"]) == len(d["content"]["panels"]) and all(p["error"] is None for p in data["panels"])
    records = next(p for p in data["panels"] if p["result"]["columns"][0]["label"] == "Records")
    assert records["result"]["rows"] == [[61]]

    # filter bar: one branch → every panel of the dataset is filtered
    f = [{"dataset_id": env["calls"], "condition": {"column": "branch", "op": "eq", "values": ["ປາກເຊ"]}}]
    fd = client.post(f"{base}/dashboards/{d['id']}/data", headers=auth["viewer"], json={"filters": f}).json()
    rec_f = next(p for p in fd["panels"] if p["panel_id"] == records["panel_id"])
    assert rec_f["result"]["rows"][0][0] < 61 and "\"branch\" AS VARCHAR) = 'ປາກເຊ'" in rec_f["result"]["sql"]

    # cross-filter from the branch bar: the clicked panel itself stays unfiltered
    bar = next(p for p in d["content"]["panels"] if p["chart"] == "bar" and p["query"]["dimensions"][0]["column"] == "branch")
    xf = [{**f[0], "source_panel": bar["id"]}]
    xd = client.post(f"{base}/dashboards/{d['id']}/data", headers=auth["viewer"], json={"filters": xf}).json()
    bar_res = next(p for p in xd["panels"] if p["panel_id"] == bar["id"])["result"]
    assert len(bar_res["rows"]) == 4 and "WHERE" not in bar_res["sql"]
    assert next(p for p in xd["panels"] if p["panel_id"] == records["panel_id"])["result"]["rows"] == rec_f["result"]["rows"]

    opts = client.get(f"{base}/dashboards/{d['id']}/filter-options", headers=auth["viewer"]).json()
    date_opt = next(o for o in opts if o["column"] == "date")
    assert date_opt["min"] <= date_opt["max"] and any(v["value"] == "ປາກເຊ" for o in opts if o["column"] == "branch" for v in o["values"])

    # edit: rename, drop a panel, add a panel from a question
    q = _ask(client, auth, env, "Average call duration per agent")
    content = d["content"]
    content["panels"] = [p for p in content["panels"] if p["id"] != bar["id"]]
    content["panels"].append({"id": "q1", "title": "Avg duration by agent", "chart": "bar", "query": q["spec"], "width": 6})
    assert client.patch(f"{base}/dashboards/{d['id']}", headers=auth["viewer"],
                        json={"revision": 1, "content": content}).status_code == 403
    up = client.patch(f"{base}/dashboards/{d['id']}", headers=auth["editor"],
                      json={"revision": 1, "name": "Branch review", "content": content})
    assert up.status_code == 200, up.text
    assert up.json()["revision"] == 2 and up.json()["name"] == "Branch review" and up.json()["origin"] == "user"
    stale = client.patch(f"{base}/dashboards/{d['id']}", headers=auth["editor"], json={"revision": 1, "content": content})
    assert stale.status_code == 409
    broken = json.loads(json.dumps(content))
    broken["panels"][-1]["query"]["measures"][0]["column"] = "ghost"
    bad = client.patch(f"{base}/dashboards/{d['id']}", headers=auth["editor"], json={"revision": 2, "content": broken})
    assert bad.status_code == 422 and "ghost" in bad.json()["error"]["message"]

    # export: one sheet per panel with the data and the SQL
    ex = client.post(f"{base}/dashboards/{d['id']}/export", headers=auth["viewer"], json={"filters": f})
    assert ex.status_code == 200 and ex.headers["content-type"].startswith(XLSX)
    wb = openpyxl.load_workbook(io.BytesIO(ex.content))
    assert wb.sheetnames[0] == "Dashboard" and len(wb.sheetnames) == 1 + len(content["panels"])
    summary = wb["Dashboard"]
    assert "ປາກເຊ" in summary["A4"].value  # active filters are recorded
    sheet = wb[wb.sheetnames[-1]]
    texts = [c.value for row in sheet.iter_rows() for c in row if isinstance(c.value, str)]
    assert any(t.startswith('SELECT "agent" AS d0, AVG("duration")') for t in texts)
    assert any("version 1" in t for t in texts)

    assert client.delete(f"{base}/dashboards/{d['id']}", headers=auth["viewer"]).status_code == 403
    assert client.delete(f"{base}/dashboards/{d['id']}", headers=auth["editor"]).status_code == 204
    assert client.get(f"{base}/dashboards/{d['id']}", headers=auth["viewer"]).status_code == 404


def test_dashboard_from_applied_blueprint(client, auth, env):
    base, src = env["base"], env["src"]
    job = client.post(f"{base}/sources/{src['id']}/blueprints", headers=auth["editor"], json={}).json()
    bp_id = job["result"]["blueprint_id"]
    client.patch(f"{base}/blueprints/{bp_id}", headers=auth["editor"], json={"edits": [{"action": "approve_all", "target": "blueprint"}]})
    assert client.post(f"{base}/blueprints/{bp_id}/apply", headers=auth["editor"]).json()["status"] == "succeeded"
    bp = client.get(f"{base}/blueprints/{bp_id}", headers=auth["viewer"]).json()
    d = client.post(f"{base}/sources/{src['id']}/dashboards", headers=auth["editor"], json={"name": "From blueprint"}).json()
    assert d["origin"] == "blueprint"
    plan = next(p for p in bp["content"]["datasets"] if p["dataset_id"] == env["calls"])
    kpi_names = [k["name"] for k in plan["kpis"] if k["status"] != "rejected"][:4]
    assert [p["title"] for p in d["content"]["panels"] if p["chart"] == "kpi_card"] == kpi_names
    data = client.post(f"{base}/dashboards/{d['id']}/data", headers=auth["viewer"], json={}).json()
    # KPI card values equal the Blueprint's KPI values (same declarative spec, now on the applied version)
    cards = {p["panel_id"]: p for p in data["panels"]}
    for panel in [p for p in d["content"]["panels"] if p["chart"] == "kpi_card"]:
        kpi = next(k for k in plan["kpis"] if k["name"] == panel["title"])
        got = cards[panel["id"]]["result"]
        assert got["dataset_version"] == 2
        if kpi["computed_on_version"] == 2:
            assert got["rows"][0][0] == pytest.approx(kpi["value"])


def _ollama(monkeypatch, replies, seen):
    monkeypatch.setattr(get_settings(), "ai_provider", "ollama")

    def handler(req):
        seen.append(json.loads(req.content))
        body = replies.pop(0)
        return httpx.Response(200, json={"model": "qwen2.5:7b", "message": {"content": json.dumps(body, ensure_ascii=False)},
                                         "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 10})

    monkeypatch.setattr(ai_mod, "http_factory", lambda t: httpx.Client(transport=httpx.MockTransport(handler)))


def _aiq(**over):
    base = {"answerable": True, "reason": "", "measures": [{"aggregation": "count_rows", "column": None, "filter_column": None,
            "filter_values": []}], "dimensions": [], "filters": [], "sort_direction": None, "limit": None,
            "analysis": "none", "chart": "bar"}
    return {**base, **over}


def test_ai_query_must_match_what_the_question_names(client, auth, env, monkeypatch):
    """Seen live with qwen2.5:7b: the model dropped the 'complaints' filter. The rule parser's reading of
    the question is used as a deterministic critic; the mismatch is fed back and the corrected query used."""
    seen = []
    wrong = _aiq(dimensions=[{"column": "branch", "grain": None}], sort_direction="desc", limit=1)
    right = _aiq(dimensions=[{"column": "branch", "grain": None}], sort_direction="desc", limit=1,
                 filters=[{"column": "type", "op": "eq", "values": ["ຮ້ອງຮຽນ"]}])
    _ollama(monkeypatch, [wrong, right], seen)
    q = _ask(client, auth, env, "Which branch had the most complaints?", engine="ai")
    assert q["engine"] == "ai" and len(seen) == 2
    assert "does not filter on them" in seen[1]["messages"][-1]["content"] and "ຮ້ອງຮຽນ" in seen[1]["messages"][-1]["content"]
    assert q["spec"]["filters"] == [{"column": "type", "op": "eq", "values": ["ຮ້ອງຮຽນ"]}]


def test_ai_query_missing_breakdown_falls_back_to_rules(client, auth, env, monkeypatch):
    seen = []
    total_only = _aiq(measures=[{"aggregation": "sum", "column": "col_8", "filter_column": None, "filter_values": []}], chart="kpi_card")
    _ollama(monkeypatch, [total_only] * 3, seen)
    q = _ask(client, auth, env, "ຄ່າໂທລວມຂອງແຕ່ລະສາຂາ", engine="ai")
    assert len(seen) == 3 and "does not group by it" in seen[1]["messages"][-1]["content"]
    assert q["engine"] == "rules" and "failed validation" in q["fallback_reason"]
    assert q["spec"]["dimensions"] == [{"column": "branch", "grain": None}]  # the correct breakdown, from the rules


def test_local_model_is_consulted_only_when_rules_cannot_answer(client, auth, env, monkeypatch):
    seen = []
    _ollama(monkeypatch, [_aiq(answerable=False, reason="The data has no weather information.")], seen)
    q = _ask(client, auth, env, "Total call cost by branch")  # engine auto + Ollama → rules first
    assert q["engine"] == "rules" and not seen and q["fallback_reason"] is None
    q = _ask(client, auth, env, "What will the weather be tomorrow?")
    assert len(seen) == 1 and q["status"] == "unanswerable" and q["engine"] == "ai" and "weather" in q["answer"]


def test_ai_trend_query_rejects_exact_date_filters_and_reads_chronologically(client, auth, env, monkeypatch):
    """Seen live: the model filtered the month starts as exact dates and sorted the series by value."""
    seen = []
    bad = _aiq(dimensions=[{"column": "date", "grain": "month"}], sort_direction="desc", analysis="change",
               filters=[{"column": "date", "op": "in", "values": ["2025-01-01", "2025-02-01", "2025-03-01"]}], chart="line")
    good = _aiq(dimensions=[{"column": "date", "grain": "month"}], sort_direction="desc", analysis="change", chart="line")
    _ollama(monkeypatch, [bad, good], seen)
    q = _ask(client, auth, env, "How did the number of calls change month by month?", engine="ai")
    assert "keeps only exact dates" in seen[1]["messages"][-1]["content"]
    assert q["engine"] == "ai" and q["spec"]["filters"] == [] and q["spec"]["sort"] is None
    direct = client.post(f"{env['base']}/datasets/{env['calls']}/query", headers=auth["viewer"],
                         json={"dataset_id": env["calls"], "measures": [{"aggregation": "count_rows"}],
                               "dimensions": [{"column": "date", "grain": "month"}]}).json()["rows"]
    assert [r[:2] for r in q["result"]["rows"]] == direct  # chronological, no exact-date filter
    assert q["answer"].startswith(f"Records went from {direct[0][1]} in January 2025 to {direct[-1][1]} in March 2025")
