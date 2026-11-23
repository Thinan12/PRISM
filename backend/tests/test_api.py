import io

import openpyxl
import pytest

from tests.excel_verify import recalc_and_compare
from tests.fixtures.make_samples import messy_call_log_xlsx

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def uploaded(client, auth, seeded):
    data, expected = messy_call_log_xlsx()
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources", headers=auth["editor"],
                    files={"file": ("ບັນທຶກ calls.xlsx", data, XLSX)})
    assert r.status_code == 201, r.text
    return r.json(), expected


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_openapi_available(client):
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/workspaces/{workspace_id}/sources" in spec["paths"]


def test_login_and_me(client, auth):
    me = client.get("/api/v1/auth/me", headers=auth["viewer"]).json()
    assert me["email"] == "viewer@prism.test" and me["workspaces"][0]["role"] == "viewer"


def test_bad_login_and_missing_token(client, seeded):
    r = client.post("/api/v1/auth/login", json={"email": "admin@prism.test", "password": "wrong-password"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.get(f"/api/v1/workspaces/{seeded['ws']}/sources").status_code == 401
    assert client.get(f"/api/v1/workspaces/{seeded['ws']}/sources", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_upload_ingests_and_builds_workbook(client, auth, seeded, uploaded):
    src, expected = uploaded
    assert src["status"] == "ready", src
    assert src["filename"] == "ບັນທຶກ calls.xlsx"
    assert [d["name"].startswith("ລາຍງານການໂທ") for d in src["datasets"]][0]
    assert src["datasets"][0]["row_count"] == len(expected)
    assert len(src["artifacts"]) == 1
    art = src["artifacts"][0]
    r = client.get(f"/api/v1/workspaces/{seeded['ws']}/artifacts/{art['id']}/download", headers=auth["viewer"])
    assert r.status_code == 200 and r.headers["content-type"] == XLSX
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert "Data" in wb.sheetnames and "Data Quality" in wb.sheetnames
    ok, bad = recalc_and_compare(r.content)
    assert not bad and ok > 100


def test_dataset_detail_rows_and_raw_layer(client, auth, seeded, uploaded):
    src, expected = uploaded
    ds_id = src["datasets"][0]["id"]
    d = client.get(f"/api/v1/workspaces/{seeded['ws']}/datasets/{ds_id}", headers=auth["viewer"]).json()
    assert d["profile"]["row_count"] == len(expected) and d["lineage"]["header_rows"] == [4, 5]
    assert "failure_examples_all" not in d["profile"]["columns"][0]
    rows = client.get(f"/api/v1/workspaces/{seeded['ws']}/datasets/{ds_id}/rows?limit=5", headers=auth["viewer"]).json()
    assert rows["total"] == len(expected) and len(rows["rows"]) == 5
    assert rows["columns"][0] == "_prism_row"
    raw = client.get(f"/api/v1/workspaces/{seeded['ws']}/datasets/{ds_id}/rows?limit=2&layer=raw", headers=auth["viewer"]).json()
    assert raw["layer"] == "raw"
    vs = client.get(f"/api/v1/workspaces/{seeded['ws']}/datasets/{ds_id}/versions", headers=auth["viewer"]).json()
    assert [v["version"] for v in vs] == [1]


def test_paste_text(client, auth, seeded):
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources/paste", headers=auth["editor"],
                    json={"text": "ສາຂາ\tຈຳນວນ\nວຽງຈັນ\t10\nປາກເຊ\t7", "name": "ທົດສອບ"})
    assert r.status_code == 201 and r.json()["status"] == "ready"
    assert r.json()["datasets"][0]["row_count"] == 2


def test_regenerate_workbook_job(client, auth, seeded, uploaded):
    src, _ = uploaded
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources/{src['id']}/workbook", headers=auth["editor"])
    assert r.status_code == 202 and r.json()["status"] == "succeeded"
    job = client.get(f"/api/v1/workspaces/{seeded['ws']}/jobs/{r.json()['id']}", headers=auth["viewer"]).json()
    assert job["result"]["artifact_id"] > 0


def test_malformed_upload_is_rejected(client, auth, seeded):
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources", headers=auth["editor"],
                    files={"file": ("evil.xlsx", b"this is not excel", XLSX)})
    assert r.status_code == 422
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources", headers=auth["editor"],
                    files={"file": ("run.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert r.status_code == 422
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources", headers=auth["editor"],
                    files={"file": ("broken.xlsx", b"PK\x03\x04" + b"\x00" * 100, XLSX)})
    assert r.status_code == 422


def test_failed_ingestion_is_recorded(client, auth, seeded):
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources", headers=auth["editor"],
                    files={"file": ("scan.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "failed" and body["error"]
    assert body["job"]["status"] == "failed"


def test_viewer_cannot_upload(client, auth, seeded):
    r = client.post(f"/api/v1/workspaces/{seeded['ws']}/sources/paste", headers=auth["viewer"], json={"text": "a,b\n1,2"})
    assert r.status_code == 403


def test_tenant_isolation(client, auth, seeded, uploaded):
    src, _ = uploaded
    ws, other = seeded["ws"], seeded["other_ws"]
    out = auth["outsider"]
    assert client.get(f"/api/v1/workspaces/{ws}/sources", headers=out).status_code == 404
    assert client.get(f"/api/v1/workspaces/{ws}/datasets/{src['datasets'][0]['id']}", headers=out).status_code == 404
    # using own workspace id with another tenant's object id must not leak either
    assert client.get(f"/api/v1/workspaces/{other}/datasets/{src['datasets'][0]['id']}", headers=out).status_code == 404
    assert client.get(f"/api/v1/workspaces/{other}/artifacts/{src['artifacts'][0]['id']}/download", headers=out).status_code == 404


def test_audit_trail(client, auth, seeded, uploaded):
    assert client.get(f"/api/v1/workspaces/{seeded['ws']}/audit", headers=auth["editor"]).status_code == 403
    evs = client.get(f"/api/v1/workspaces/{seeded['ws']}/audit", headers=auth["admin"]).json()
    actions = {e["action"] for e in evs}
    assert {"source.upload", "source.ingested", "workbook.generated", "artifact.download"} <= actions
