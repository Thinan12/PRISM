import hashlib
import io

import openpyxl
import pytest

from app.excel.builder import WorkbookDataset, WorkbookSource, build_workbook
from app.ingestion.pipeline import ingest
from app.profiling.profiler import profile_table
from tests.excel_verify import recalc_and_compare
from tests.fixtures.make_samples import messy_call_log_xlsx


@pytest.fixture(scope="module")
def workbook():
    data, expected = messy_call_log_xlsx()
    res = ingest(data, "calls.xlsx")
    dss = [WorkbookDataset(name=t.name, columns=t.columns, df=t.clean, profile=profile_table(t), lineage=t.lineage) for t in res.tables]
    out = build_workbook(dss, WorkbookSource("calls.xlsx", "xlsx", hashlib.sha256(data).hexdigest(), len(data), data))
    return out, expected, res


def test_sheets_present(workbook):
    out, _, _ = workbook
    wb = openpyxl.load_workbook(io.BytesIO(out))
    assert wb.sheetnames == [
        "README", "Data", "Data 2", "Summary", "Analysis", "Data Quality", "Lookups", "Excluded Rows",
        "Src-ບັນທຶກການໂທ", "Src-Branches",
    ]


def test_data_sheet_table_types_and_lao(workbook):
    out, expected, _ = workbook
    wb = openpyxl.load_workbook(io.BytesIO(out))
    ws = wb["Data"]
    assert "tbl_Data" in ws.tables
    tbl = ws.tables["tbl_Data"]
    assert tbl.ref == f"A1:L{len(expected) + 1}"
    headers = [c.value for c in ws[1]]
    assert headers[:3] == ["ລ/ດ", "ວັນທີ Date", "ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch"]
    assert headers[-3:] == ["Source sheet", "Source row", "PRISM flags"]
    # typed values, formats and Lao font
    first = expected[0]
    assert ws["B2"].value.date() == first["date"].date() and ws["B2"].number_format == "yyyy-mm-dd"
    assert ws["C2"].value == first["branch"] and ws["C2"].font.name == "Noto Sans Lao"
    assert round(ws["G2"].value.total_seconds()) == first["duration"] and ws["G2"].number_format == "[h]:mm:ss"
    assert ws["H2"].value == first["cost"] and "₭" in ws["H2"].number_format
    assert isinstance(ws["E3"].value, str) and ws["E3"].value.startswith("020")  # phone kept as text
    # duplicate flagged with lineage
    assert "duplicate of source row" in ws.cell(len(expected) + 1, 12).value
    assert ws.freeze_panes == "A2"
    assert ws.data_validations.dataValidation, "validation rules expected"
    assert any(dv.type == "list" for dv in ws.data_validations.dataValidation)


def test_every_formula_recalculates_to_prism_value(workbook):
    out, _, _ = workbook
    ok, bad = recalc_and_compare(out)
    assert not bad, "\n".join(bad[:20])
    assert ok > 150


def test_summary_values_match_source_data(workbook):
    out, expected, _ = workbook
    wb = openpyxl.load_workbook(io.BytesIO(out), data_only=True)
    ws = wb["Summary"]
    cells = {}
    for r in range(1, ws.max_row + 1):  # first dataset section wins
        cells.setdefault(ws.cell(r, 1).value, ws.cell(r, 2).value)
    assert cells["Records"] == len(expected)
    assert cells["ຄ່າໃຊ້ຈ່າຍ / ຄ່າໂທ (ກີບ) — total"] == sum(r["cost"] for r in expected)
    assert round(cells["ຄ່າໃຊ້ຈ່າຍ / Duration — total"].total_seconds()) == sum(r["duration"] for r in expected)


def test_quality_and_excluded_sheets(workbook):
    out, _, _ = workbook
    wb = openpyxl.load_workbook(io.BytesIO(out), data_only=True)
    q = wb["Data Quality"]
    texts = [str(c.value) for row in q.iter_rows() for c in row if c.value is not None]
    assert texts.count("✓ matches") == 2
    assert any("duplicate" in t for t in texts)
    ex = wb["Excluded Rows"]
    reasons = [ex.cell(r, 3).value for r in range(5, ex.max_row + 1)]
    assert reasons.count("total row") == 2


def test_source_sheet_preserved(workbook):
    out, _, _ = workbook
    wb = openpyxl.load_workbook(io.BytesIO(out))
    src = wb["Src-ບັນທຶກການໂທ"]
    assert src["A1"].value.startswith("ລາຍງານການໂທ")
    assert "A1:I1" in {str(m) for m in src.merged_cells.ranges}
    assert "C4:D4" in {str(m) for m in src.merged_cells.ranges}
    assert src["A1"].font.b


def test_csv_workbook_without_source_copies():
    data = "Branch,Calls,Revenue (LAK),Date\nVTE,10,\"1,500,000\",2025-01-03\nLPB,7,\"950,000\",2025-02-11\nVTE,3,\"200,000\",2025-02-12\n".encode()
    res = ingest(data, "s.csv")
    dss = [WorkbookDataset(name=t.name, columns=t.columns, df=t.clean, profile=profile_table(t), lineage=t.lineage) for t in res.tables]
    out = build_workbook(dss, WorkbookSource("s.csv", "csv", "0" * 64, len(data)))
    wb = openpyxl.load_workbook(io.BytesIO(out))
    assert not any(n.startswith("Src-") for n in wb.sheetnames) and "Excluded Rows" not in wb.sheetnames
    ok, bad = recalc_and_compare(out)
    assert not bad and ok > 10
