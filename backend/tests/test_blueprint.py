import io
import json
from types import SimpleNamespace

import anthropic
import httpx
import openpyxl
import pytest

from app.blueprint import ai as ai_mod
from app.blueprint.ai import AIInterpretation, api_schema, build_payload
from app.blueprint.context import DatasetContext
from app.blueprint.kpi import KpiInvalid, compile_kpi, evaluate_kpis, validate_kpi
from app.blueprint.rules import rules_blueprint
from app.blueprint.schema import Aggregation, Filter, Kpi
from app.blueprint.service import build_blueprint
from app.core.config import get_settings
from app.ingestion.pipeline import ingest
from app.profiling.profiler import profile_table
from tests.excel_verify import recalc_and_compare
from tests.fixtures.make_samples import messy_call_log_xlsx

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def contexts():
    res = ingest(messy_call_log_xlsx()[0], "calls.xlsx")
    return [
        DatasetContext(dataset_id=i + 1, version=1, version_id=i + 1, name=t.name, columns=t.columns,
                       profile=profile_table(t), lineage=t.lineage, df=t.clean)
        for i, t in enumerate(res.tables)
    ]


def _cols(ctx):
    return {c["label"]: c["key"] for c in ctx.columns}


# ---------------------------------------------------------------- schema & rules


def test_api_schema_is_structured_output_compatible():
    schema = api_schema()
    bad = []

    def walk(n, path="$"):
        if isinstance(n, dict):
            if n.get("type") == "object" and "properties" in n:
                if n.get("additionalProperties") is not False:
                    bad.append(f"{path}: additionalProperties")
                if set(n.get("required", [])) != set(n["properties"]):
                    bad.append(f"{path}: required")
            for k in ("minimum", "maximum", "minLength", "maxLength", "pattern", "default"):
                if k in n and not isinstance(n[k], dict):
                    bad.append(f"{path}: {k}")
            for k, v in n.items():
                walk(v, f"{path}.{k}")
        elif isinstance(n, list):
            for i, v in enumerate(n):
                walk(v, f"{path}[{i}]")

    walk(schema)
    assert not bad, bad


def test_rules_blueprint_on_messy_call_log(contexts):
    bp = rules_blueprint(1, contexts)
    assert bp.domain.label == "Call center / call log"
    calls = bp.datasets[0]
    roles = {c.display_name: c.semantic_role.value for c in calls.columns}
    assert roles["ສາຂາ Branch"] == "organization_unit"
    assert roles["ພະນັກງານ Agent"] == "person"
    assert roles["Duration"] == "duration" and roles["ຄ່າໂທ (ກີບ)"] == "currency_amount"
    kinds = {o.spec.op for o in calls.operations}
    assert kinds == {"map_values", "remove_duplicate_rows"}
    assert all(o.status == "proposed" for o in calls.operations)  # nothing runs without approval
    rel = bp.relationships[0]
    assert rel.verified and rel.match_ratio == 1.0 and rel.to_dataset == 2
    assert {p.chart.value for p in bp.dashboard} >= {"kpi_card", "line", "bar", "donut"}


def test_kpi_values_are_deterministic_and_correct(contexts):
    ctx = contexts[0]
    k = _cols(ctx)
    bp = rules_blueprint(1, contexts)
    plan = bp.datasets[0]
    evaluate_kpis(plan.kpis, ctx)
    vals = {x.name: x.value for x in plan.kpis}
    assert vals["Records"] == len(ctx.df)
    assert vals["Total ຄ່າໂທ (ກີບ)"] == float(ctx.df[k["ຄ່າໃຊ້ຈ່າຍ / ຄ່າໂທ (ກີບ)"]].sum())
    assert vals["Average Duration"] == pytest.approx(float(ctx.df[k["ຄ່າໃຊ້ຈ່າຍ / Duration"]].mean()))
    assert vals["Distinct ພະນັກງານ Agent"] == ctx.df[k["ຂໍ້ມູນລູກຄ້າ / ພະນັກງານ Agent"]].nunique()
    share = next(x for x in plan.kpis if x.aggregation == Aggregation.share_of_rows)
    st = ctx.df[share.filter.column]
    assert share.value == pytest.approx((st == share.filter.values[0]).sum() / len(st))
    assert all(x.sql and x.sql.startswith("SELECT") for x in plan.kpis)


def test_kpi_validation_and_sql_safety(contexts):
    ctx = contexts[0]
    k = _cols(ctx)
    with pytest.raises(KpiInvalid):
        validate_kpi(Kpi(id="x", name="x", aggregation=Aggregation.sum, column=k["ສະຖານະ"], confidence=1, origin="user"), ctx)
    with pytest.raises(KpiInvalid):
        validate_kpi(Kpi(id="x", name="x", aggregation=Aggregation.sum, column="nope", confidence=1, origin="user"), ctx)
    evil = "x'); DROP TABLE data; --"
    kp = Kpi(id="x", name="x", aggregation=Aggregation.count_rows, filter=Filter(column=k["ສະຖານະ"], values=[evil]), confidence=1, origin="user")
    with pytest.raises(KpiInvalid):  # value not present in the data
        validate_kpi(kp, ctx)
    sql, params, disp = compile_kpi(kp, ctx)
    assert evil not in sql and params == [evil] and "''" in disp  # parameterized; display escaped


def test_payload_is_profile_only_and_bounded(contexts):
    bp = rules_blueprint(1, contexts)
    payload = build_payload(contexts, bp, "Focus on branch performance")
    text = json.dumps(payload, ensure_ascii=False)
    for ds in payload["datasets"]:
        assert "rows" not in ds and "data" not in ds
        for c in ds["columns"]:
            assert len(c["samples"]) <= 5 and len(c.get("top_values", [])) <= 8
    phones = contexts[0].df[_cols(contexts[0])["ການໂທ / ເບີໂທ Phone"]].dropna().tolist()
    assert not any(p in text for p in phones), "phone numbers must never be sent"
    assert len(text) < 30_000


def test_payload_without_values_contains_no_cell_values(contexts):
    bp = rules_blueprint(1, contexts)
    payload = build_payload(contexts, bp, send_values=False)
    text = json.dumps(payload, ensure_ascii=False)
    df = contexts[0].df
    for key in (_cols(contexts[0])["ຂໍ້ມູນລູກຄ້າ / ພະນັກງານ Agent"], _cols(contexts[0])["ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch"]):
        for v in df[key].dropna().unique():
            assert f'"{v}"' not in text, v
    assert payload["values_included"] is False and all(not c["samples"] for d in payload["datasets"] for c in d["columns"])


# ---------------------------------------------------------------- AI path (fake client)


def _interp(ctx_calls, ctx_branches, **over):
    k = _cols(ctx_calls)
    data = {
        "domain": {"label": "Telecom customer-service call log", "confidence": 0.92, "rationale": "Calls with duration, agent, branch."},
        "datasets": [{
            "dataset_ref": "1", "title": "Q1 2025 call log", "summary": "Inbound service calls by branch and agent.",
            "columns": [
                {"key": k["ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch"], "display_name": "ສາຂາ Branch", "description": "Branch handling the call.",
                 "semantic_role": "organization_unit", "confidence": 0.95, "rationale": "Lao 'ສາຂາ' = branch."},
                {"key": k["ຄ່າໃຊ້ຈ່າຍ / ຄ່າໂທ (ກີບ)"], "display_name": "Call cost (₭)", "description": "Charge per call in kip.",
                 "semantic_role": "currency_amount", "confidence": 0.9, "rationale": "Kip amounts."},
                {"key": "ghost_column", "display_name": "Ghost", "description": "", "semantic_role": "other", "confidence": 0.1, "rationale": ""},
            ],
            "kpis": [
                {"name": "Total call cost", "description": "", "aggregation": "sum", "column": k["ຄ່າໃຊ້ຈ່າຍ / ຄ່າໂທ (ກີບ)"],
                 "filter_column": None, "filter_values": [], "confidence": 0.9, "rationale": ""},
                {"name": "Complaint calls", "description": "", "aggregation": "count_rows", "column": None,
                 "filter_column": k["ການໂທ / ປະເພດ Type"], "filter_values": ["ຮ້ອງຮຽນ"], "confidence": 0.8, "rationale": ""},
                {"name": "Sum of status", "description": "", "aggregation": "sum", "column": k["ສະຖານະ"],
                 "filter_column": None, "filter_values": [], "confidence": 0.3, "rationale": "invalid on purpose"},
            ],
            "value_mappings": [{"column": k["ຂໍ້ມູນລູກຄ້າ / ພະນັກງານ Agent"], "canonical": "Khamla", "variants": ["khamla", "KHAMLA-not-there"],
                                "confidence": 0.9, "rationale": "Same agent, different case."}],
            "exclude_columns": [],
        }],
        "relationships": [
            {"from_dataset": "1", "from_column": k["ສະຖານະ"], "to_dataset": "2", "to_column": _cols(ctx_branches)["Manager"],
             "confidence": 0.4, "rationale": "nonsense on purpose"},
        ],
        "dashboard": [{"dataset_ref": "1", "title": "Cost by branch", "chart": "bar", "kpi_names": ["Total call cost"],
                       "dimension_column": k["ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch"], "time_column": None}],
        "assumptions": [{"text": "Costs are per call, not per minute.", "confidence": 0.6}],
    }
    data.update(over)
    return data


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(text, stop="end_turn"):
    block = SimpleNamespace(type="text", text=text, to_dict=lambda: {"type": "text", "text": text})
    return SimpleNamespace(stop_reason=stop, content=[block], usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
                           model="claude-opus-5-5", stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None)


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")

    def use(responses):
        fake = FakeClient(responses)
        monkeypatch.setattr(ai_mod, "client_factory", lambda: fake)
        return fake

    return use


SRC = SimpleNamespace(id=1)


def test_ai_interpretation_is_validated_against_data(ai_on, contexts):
    fake = ai_on([_resp(json.dumps(_interp(*contexts), ensure_ascii=False))])
    bp = build_blueprint(SRC, contexts)
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert bp.generator.engine == "ai" and bp.generator.attempts == 1
    assert bp.domain.label == "Telecom customer-service call log" and bp.domain.origin == "ai"
    plan = bp.datasets[0]
    cost = next(c for c in plan.columns if c.display_name == "Call cost (₭)")
    assert cost.origin == "ai" and cost.status == "proposed"
    names = {k.name: k for k in plan.kpis}
    assert "Sum of status" not in names  # impossible aggregation dropped
    assert names["Complaint calls"].value == float((contexts[0].df[names["Complaint calls"].filter.column] == "ຮ້ອງຮຽນ").sum())
    assert names["Total call cost"].origin == "ai"  # same computation as the rules KPI: renamed, not duplicated
    w = " ".join(bp.generator.warnings)
    assert "ghost_column" in w and "Sum of status" in w and "KHAMLA-not-there" in w and "dropped" in w
    maps = [o for o in plan.operations if o.spec.op == "map_values"]
    assert len(maps) == 1 and maps[0].origin == "ai" and maps[0].spec.mappings[0].variants == ["khamla"]
    assert all(r.origin == "rules" for r in bp.relationships)  # unmeasurable AI relationship rejected
    assert bp.dashboard[0].title == "Cost by branch" and bp.dashboard[0].kpi_ids


def test_ai_retries_after_invalid_output(ai_on, contexts):
    fake = ai_on([_resp("{not json"), _resp(json.dumps(_interp(*contexts), ensure_ascii=False))])
    bp = build_blueprint(SRC, contexts)
    assert bp.generator.engine == "ai" and bp.generator.attempts == 2
    retry_msgs = fake.calls[1]["messages"]
    assert retry_msgs[-1]["role"] == "user" and "invalid" in retry_msgs[-1]["content"]


def test_ai_persistently_invalid_falls_back_to_rules(ai_on, contexts):
    bad = json.dumps({"domain": {"label": "x"}})
    ai_on([_resp(bad), _resp(bad), _resp(bad)])
    bp = build_blueprint(SRC, contexts)
    assert bp.generator.engine == "rules" and "failed validation" in bp.generator.fallback_reason
    assert bp.datasets[0].kpis[0].value == len(contexts[0].df)


@pytest.mark.parametrize("response,reason", [
    (_resp("", stop="refusal"), "declined"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), "network"),
])
def test_ai_failures_fall_back(ai_on, contexts, response, reason):
    ai_on([response])
    bp = build_blueprint(SRC, contexts)
    assert bp.generator.engine == "rules" and reason in bp.generator.fallback_reason


def test_ai_not_configured(contexts):
    assert not get_settings().anthropic_api_key
    bp = build_blueprint(SRC, contexts)
    assert bp.generator.engine == "rules" and "not configured" in bp.generator.fallback_reason


def test_ai_output_model_rejects_extra_fields(contexts):
    data = _interp(*contexts)
    data["domain"]["numbers"] = 42
    with pytest.raises(Exception):
        AIInterpretation.model_validate(data)


# ---------------------------------------------------------------- API flow


@pytest.fixture(scope="module")
def source(client, auth, seeded):
    data, _ = messy_call_log_xlsx()
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources", headers=auth["editor"], files={"file": ("bp.xlsx", data, XLSX)})
    assert r.status_code == 201 and r.json()["status"] == "ready"
    return r.json()


def _bp(client, auth, seeded, source):
    r = client.get(f"/api/v1/workspaces/{seeded['ws']}/sources/{source['id']}/blueprint", headers=auth["viewer"])
    assert r.status_code == 200, r.text
    return r.json()


def test_blueprint_api_full_flow(client, auth, seeded, source):
    ws = seeded["ws"]
    base = f"/api/v1/workspaces/{ws}"
    assert client.get(f"{base}/ai-status", headers=auth["viewer"]).json()["configured"] is False
    none = client.get(f"{base}/sources/{source['id']}/blueprint", headers=auth["viewer"])
    assert none.status_code == 200 and none.json() is None
    assert client.post(f"{base}/sources/{source['id']}/blueprints", headers=auth["viewer"], json={}).status_code == 403
    r = client.post(f"{base}/sources/{source['id']}/blueprints", headers=auth["editor"], json={"requirements": "branch performance"})
    assert r.status_code == 202 and r.json()["status"] == "succeeded", r.text
    bp = _bp(client, auth, seeded, source)
    assert bp["status"] == "draft" and bp["engine"] == "rules"
    calls_id = source["datasets"][0]["id"]
    plan = next(p for p in bp["content"]["datasets"] if p["dataset_id"] == calls_id)
    dedupe = next(o for o in plan["operations"] if o["spec"]["op"] == "remove_duplicate_rows")
    mapping = next(o for o in plan["operations"] if o["spec"]["op"] == "map_values")
    col_branch = next(c for c in plan["columns"] if c["display_name"] == "ສາຂາ Branch")
    status_key = next(c["key"] for c in plan["columns"] if c["original_label"] == "ສະຖານະ")
    url = f"{base}/blueprints/{bp['id']}"

    # invalid edits are rejected with clear errors
    r = client.patch(url, headers=auth["editor"], json={"edits": [{"action": "update", "target": "column", "dataset_id": calls_id,
                                                                     "id": col_branch["key"], "changes": {"display_name": "Duration"}}]})
    assert r.status_code == 422 and "Duplicate" in r.json()["error"]["message"]
    r = client.patch(url, headers=auth["editor"], json={"edits": [{"action": "update", "target": "kpi", "dataset_id": calls_id,
                                                                     "id": plan["kpis"][0]["id"], "changes": {"value": 999}}]})
    assert r.status_code == 422
    assert client.patch(url, headers=auth["viewer"], json={"edits": [{"action": "approve_all", "target": "blueprint"}]}).status_code == 403

    edits = [
        {"action": "update", "target": "column", "dataset_id": calls_id, "id": col_branch["key"], "changes": {"display_name": "Branch ສາຂາ"}},
        {"action": "approve", "target": "operation", "dataset_id": calls_id, "id": dedupe["id"]},
        {"action": "update", "target": "operation", "dataset_id": calls_id, "id": mapping["id"],
         "changes": {"mappings": [{"to": "Khamla", "variants": ["khamla"]}]}},
        {"action": "approve", "target": "operation", "dataset_id": calls_id, "id": mapping["id"]},
        {"action": "add", "target": "kpi", "dataset_id": calls_id,
         "changes": {"name": "Unsuccessful calls", "aggregation": "count_rows", "filter": {"column": status_key, "values": ["ບໍ່ສຳເລັດ"]}}},
        {"action": "approve_all", "target": "blueprint"},
    ]
    r = client.patch(url, headers=auth["editor"], json={"edits": edits})
    assert r.status_code == 200, r.text
    content = r.json()["content"]
    plan = next(p for p in content["datasets"] if p["dataset_id"] == calls_id)
    added = next(k for k in plan["kpis"] if k["name"] == "Unsuccessful calls")
    assert added["origin"] == "user" and added["value"] > 0 and "IN ('ບໍ່ສຳເລັດ')" in added["sql"]
    assert all(o["status"] == "approved" for o in plan["operations"])

    r = client.post(f"{url}/apply", headers=auth["editor"])
    assert r.status_code == 202 and r.json()["status"] == "succeeded", r.text
    result = r.json()["result"]
    calls_res = next(d for d in result["datasets"] if d["dataset_id"] == calls_id)
    assert calls_res["version"] == 2 and calls_res["row_count"] == source["datasets"][0]["row_count"] - 1

    d = client.get(f"{base}/datasets/{calls_id}", headers=auth["viewer"]).json()
    assert d["current_version"] == 2
    assert any(c["label"] == "Branch ສາຂາ" for c in d["columns"])
    assert d["profile"]["duplicate_rows"] == 0
    assert not any(i["code"] == "inconsistent_spelling" for i in d["profile"]["issues"])
    assert any("duplicate removed" in e["reason"] for e in d["lineage"]["excluded_rows"])
    assert d["lineage"]["blueprint"]["blueprint_id"] == bp["id"]
    v1 = client.get(f"{base}/datasets/{calls_id}?version=1", headers=auth["viewer"]).json()
    assert v1["profile"]["duplicate_rows"] == 1  # history preserved

    # workbook: KPI + Blueprint sheets, every formula verified
    art = result["artifact_id"]
    wb_bytes = client.get(f"{base}/artifacts/{art}/download", headers=auth["viewer"]).content
    wb = openpyxl.load_workbook(io.BytesIO(wb_bytes))
    assert "KPIs" in wb.sheetnames and "Blueprint" in wb.sheetnames
    assert wb["Data"]["C1"].value == "Branch ສາຂາ"
    ok, bad = recalc_and_compare(wb_bytes)
    assert not bad, bad[:5]
    kws = openpyxl.load_workbook(io.BytesIO(wb_bytes), data_only=True)["KPIs"]
    kpi_rows = {kws.cell(r, 1).value: (kws.cell(r, 2).value, kws.cell(r, 4).value) for r in range(1, kws.max_row + 1)}
    assert kpi_rows["Unsuccessful calls"][1] == "✓"

    # applied blueprints are immutable; audit trail present
    r = client.patch(url, headers=auth["editor"], json={"edits": [{"action": "approve_all", "target": "blueprint"}]})
    assert r.status_code == 409
    assert client.post(f"{url}/apply", headers=auth["editor"]).status_code == 409
    actions = {e["action"] for e in client.get(f"{base}/audit?limit=500", headers=auth["admin"]).json()}
    assert {"blueprint.generated", "blueprint.edited", "blueprint.apply_requested", "blueprint.applied"} <= actions


def test_blueprint_tenant_isolation(client, auth, seeded, source):
    other = seeded["other_ws"]
    bp = client.get(f"/api/v1/workspaces/{seeded['ws']}/sources/{source['id']}/blueprints", headers=auth["viewer"]).json()
    assert bp
    assert client.get(f"/api/v1/workspaces/{other}/blueprints/{bp[0]['id']}", headers=auth["outsider"]).status_code == 404
    assert client.post(f"/api/v1/workspaces/{other}/blueprints/{bp[0]['id']}/apply", headers=auth["outsider"]).status_code == 404


def test_stale_blueprint_cannot_be_applied(client, auth, seeded, source):
    base = f"/api/v1/workspaces/{seeded['ws']}"
    # generate a fresh draft on the current (v2) data, then make it stale by applying another blueprint
    r1 = client.post(f"{base}/sources/{source['id']}/blueprints", headers=auth["editor"], json={})
    first = r1.json()["result"]["blueprint_id"]
    r2 = client.post(f"{base}/sources/{source['id']}/blueprints", headers=auth["editor"], json={})
    second = r2.json()["result"]["blueprint_id"]
    assert client.get(f"{base}/blueprints/{first}", headers=auth["viewer"]).json()["status"] == "superseded"
    assert client.post(f"{base}/blueprints/{second}/apply", headers=auth["editor"]).json()["status"] == "succeeded"
    # a blueprint generated before the new version must not be applicable
    client.post(f"{base}/sources/{source['id']}/blueprints", headers=auth["editor"], json={})
    third = client.get(f"{base}/sources/{source['id']}/blueprint", headers=auth["viewer"]).json()
    from app.db.models import BlueprintRecord
    from app.db.session import get_sessionmaker

    with get_sessionmaker()() as db:  # simulate data changing after generation
        rec = db.get(BlueprintRecord, third["id"])
        c = dict(rec.content)
        c["datasets"] = [{**p, "version": p["version"] - 1} for p in c["datasets"]]
        rec.content = c
        db.commit()
    job = client.post(f"{base}/blueprints/{third['id']}/apply", headers=auth["editor"]).json()
    assert job["status"] == "failed" and "changed" in job["error"]
