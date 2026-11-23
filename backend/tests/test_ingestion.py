import io
import json

import openpyxl
import pytest

from app.core.errors import IngestionError
from app.ingestion.pipeline import ingest
from app.ingestion.readers import sniff_kind
from app.profiling.profiler import profile_table
from tests.fixtures.make_samples import messy_call_log_xlsx


def _xlsx(build) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


@pytest.fixture(scope="module")
def messy():
    data, expected = messy_call_log_xlsx()
    return ingest(data, "calls.xlsx"), expected


def test_messy_call_log_structure(messy):
    res, expected = messy
    assert res.kind == "xlsx"
    assert [s["name"] for s in res.sheets] == ["ບັນທຶກການໂທ", "Branches"]
    calls, branches = res.tables
    assert len(calls.clean) == len(expected)  # every record, incl. the duplicate, nothing invented
    assert calls.title.startswith("ລາຍງານການໂທ")
    assert calls.lineage["header_rows"] == [4, 5]
    labels = [c["label"] for c in calls.columns]
    assert labels[2] == "ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch"  # two-level merged header combined
    types = {c["label"]: (c["logical_type"], c["unit"]) for c in calls.columns}
    assert types["ວັນທີ Date"] == ("date", None)
    assert types["ການໂທ / ເບີໂທ Phone"][0] == "phone"
    assert types["ຄ່າໃຊ້ຈ່າຍ / Duration"] == ("duration", "seconds")
    assert types["ຄ່າໃຊ້ຈ່າຍ / ຄ່າໂທ (ກີບ)"] == ("integer", "LAK")
    reasons = [e["reason"] for e in calls.lineage["excluded_rows"]]
    assert reasons.count("total row") == 2 and "note row below table" in reasons
    assert len(branches.clean) == 4


def test_messy_values_exact(messy):
    res, expected = messy
    df = res.tables[0].clean
    keys = {c["label"]: c["key"] for c in res.tables[0].columns}
    for i, rec in enumerate(expected):
        row = df.iloc[i]
        assert row[keys["ວັນທີ Date"]].to_pydatetime() == rec["date"], i
        assert int(row[keys["ຄ່າໃຊ້ຈ່າຍ / Duration"]]) == rec["duration"], i
        assert int(row[keys["ຄ່າໃຊ້ຈ່າຍ / ຄ່າໂທ (ກີບ)"]]) == rec["cost"], i
        assert row[keys["ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch"]] == rec["branch"], i  # whitespace trimmed, Lao preserved
    assert df[keys["ການໂທ / ເບີໂທ Phone"]].dropna().str.startswith("020").all()


def test_messy_profile(messy):
    res, _ = messy
    p = profile_table(res.tables[0])
    assert p["duplicate_rows"] == 1
    assert all(tc["matches"] for tc in p["total_checks"]) and len(p["total_checks"]) == 2
    codes = {i["code"] for i in p["issues"]}
    assert {"duplicate_rows", "inconsistent_spelling", "missing_values"} <= codes
    assert 0 < p["quality_score"] < 100
    assert sum(c["parse_failures"] for c in p["columns"]) == 0


def test_total_mismatch_detected():
    def build(wb):
        ws = wb.active
        ws.append(["Item", "Amount"])
        for i in range(5):
            ws.append([f"item {i}", 100])
        ws.append(["Total", 999])
    res = ingest(_xlsx(build), "t.xlsx")
    p = profile_table(res.tables[0])
    assert len(res.tables[0].clean) == 5
    assert p["total_checks"][0]["matches"] is False and p["total_checks"][0]["computed"] == 500
    assert any(i["code"] == "total_mismatch" and i["severity"] == "error" for i in p["issues"])


def test_parse_failures_are_reported_not_dropped():
    def build(wb):
        ws = wb.active
        ws.append(["Date", "Amount"])
        for i in range(20):
            ws.append([f"{i + 1:02d}/01/2025", 1000 + i])
        ws.append(["not a date", "oops"])
    res = ingest(_xlsx(build), "f.xlsx")
    t = res.tables[0]
    assert len(t.clean) == 21
    assert t.failures[t.columns[0]["key"]] == [(22, "not a date")]
    assert t.raw[t.columns[1]["key"]].iloc[-1] == "oops"  # original preserved in raw layer


def test_side_by_side_and_stacked_tables():
    def build(wb):
        ws = wb.active
        ws["A1"], ws["B1"] = "Name", "Score"
        ws["A2"], ws["B2"] = "a", 1
        ws["A3"], ws["B3"] = "b", 2
        ws["E1"], ws["F1"], ws["G1"] = "Code", "City", "Opened"
        ws["E2"], ws["F2"], ws["G2"] = "X1", "Vientiane", "2020-01-01"
        ws["A8"], ws["B8"] = "Region", "Target"
        ws["A9"], ws["B9"] = "North", 10
        ws["A10"], ws["B10"] = "South", 12
    res = ingest(_xlsx(build), "multi.xlsx")
    assert len(res.tables) == 3
    assert sorted(len(t.clean) for t in res.tables) == [1, 2, 2]


def test_blank_row_inside_table_is_continuation():
    def build(wb):
        ws = wb.active
        ws.append(["Agent", "Calls", "Date"])
        ws.append(["A", 3, "01/01/2025"])
        ws.append(["B", 4, "02/01/2025"])
        ws.append([])
        ws.append(["C", 5, "03/01/2025"])
    res = ingest(_xlsx(build), "gap.xlsx")
    assert len(res.tables) == 1 and len(res.tables[0].clean) == 3


def test_no_header_gets_generic_names():
    res = ingest(b"1,2,3\n4,5,6\n7,8,9\n", "n.csv")
    t = res.tables[0]
    assert len(t.clean) == 3 and t.columns[0]["label"].startswith("Column")


def test_csv_semicolon_and_encoding():
    text = "ຊື່;ຈຳນວນ;ວັນທີ\nສົມສັກ;1.500;31/12/2024\nບຸນມີ;2.000;01/01/2025\n"
    res = ingest(text.encode("utf-8-sig"), "lao.csv")
    t = res.tables[0]
    assert [c["label"] for c in t.columns] == ["ຊື່", "ຈຳນວນ", "ວັນທີ"]
    assert t.clean.iloc[0, 1] == "ສົມສັກ"


def test_tsv_and_pasted_text():
    res = ingest("Branch\tCalls\nVTE\t10\nLPB\t7\n".encode(), "x.tsv")
    assert res.kind == "tsv" and len(res.tables[0].clean) == 2
    md = "| Branch | Calls |\n|---|---|\n| VTE | 10 |\n| LPB | 7 |\n"
    res = ingest(md.encode(), "paste.txt")
    assert res.tables[0].columns[1]["logical_type"] == "integer"


def test_json_records_nested():
    data = {"meta": {"v": 1}, "calls": [{"id": 1, "agent": {"name": "ສົມສັກ"}, "dur": "00:01:10"}, {"id": 2, "agent": {"name": "Vanh"}, "dur": "00:02:00"}]}
    res = ingest(json.dumps(data, ensure_ascii=False).encode(), "d.json")
    t = res.tables[0]
    assert [c["label"] for c in t.columns] == ["id", "agent.name", "dur"]
    assert t.columns[2]["logical_type"] == "duration"
    assert list(t.clean[t.columns[2]["key"]]) == [70, 120]


def test_pdf_table():
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    with pdf.table() as table:
        for row in [["Branch", "Calls", "Revenue"], ["VTE", "120", "1,500,000"], ["LPB", "80", "950,000"], ["PKZ", "45", "600,000"]]:
            r = table.row()
            for cell in row:
                r.cell(cell)
    res = ingest(bytes(pdf.output()), "report.pdf")
    t = res.tables[0]
    assert res.kind == "pdf" and len(t.clean) == 3
    assert t.columns[2]["logical_type"] == "integer" and int(t.clean[t.columns[2]["key"]].sum()) == 3_050_000


def test_legacy_xls_with_merges():
    import xlwt

    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("Calls")
    ws.write_merge(0, 0, 0, 2, "ລາຍງານ Report")
    for j, h in enumerate(["ສາຂາ", "Calls", "Date"]):
        ws.write(1, j, h)
    rows = [("ວຽງຈັນ", 10, "01/01/2025"), ("ປາກເຊ", 7, "02/01/2025")]
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            ws.write(2 + i, j, v)
    b = io.BytesIO()
    wb.save(b)
    res = ingest(b.getvalue(), "old.xls")
    t = res.tables[0]
    assert res.kind == "xls" and t.title == "ລາຍງານ Report" and len(t.clean) == 2
    assert t.columns[1]["logical_type"] == "integer"


def test_malformed_and_mismatched_files():
    with pytest.raises(IngestionError):
        ingest(b"PK\x03\x04garbage", "bad.xlsx")
    with pytest.raises(IngestionError):
        sniff_kind(b"hello,world\n1,2\n", "fake.pdf")
    with pytest.raises(IngestionError):
        ingest(b"\x00\x01\x02\x03binary", "x.bin")
    with pytest.raises(IngestionError):
        ingest(b"%PDF-1.4 not really", "broken.pdf")
    with pytest.raises(IngestionError):
        ingest(b"\x89PNG\r\n\x1a\n....", "photo.png")  # no OCR configured → clear error


def test_hidden_and_empty_sheets():
    def build(wb):
        wb.active.append(["a", "b"])
        wb.active.append([1, 2])
        wb.create_sheet("Empty")
    res = ingest(_xlsx(build), "e.xlsx")
    assert len(res.tables) == 1
