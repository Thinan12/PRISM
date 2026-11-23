"""Professional Excel workbook generation.

Sheets: README · Data (one per dataset) · Summary · Analysis · Data Quality · Lookups ·
Excluded Rows · Src-<original sheet> (copies of the uploaded workbook).

Every aggregate is a live Excel formula (A1 references, see ARCHITECTURE ADR-2). Formulas
are written together with the value PRISM computed deterministically, which Excel uses as
the cached result until it recalculates; a "PRISM check" column compares the two so any
divergence (e.g. after a user edits the data) is visible.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

from app.ingestion.parsers import has_lao

TZ = ZoneInfo("Asia/Vientiane")
LAO_FONT = "Noto Sans Lao"
BASE_FONT = "Calibri"
EXCEL_MAX_ROWS = 1_048_575
MAX_CATEGORY = 50

# Night Observatory accents adapted for print-friendly spreadsheets
NAVY, NAVY_2, INK, MUTED = "#121A2C", "#1A2439", "#1B2233", "#5B6782"
AURORA, AURORA_BG = "#127A5C", "#E3F7F0"
EMBER, EMBER_BG = "#B4501A", "#FFF0E6"
NEBULA, NEBULA_BG = "#6A4FD1", "#F0EBFF"
FLARE_BG = "#FFE3E8"
LINE = "#D5DAE5"


@dataclass
class WorkbookDataset:
    name: str
    columns: list[dict[str, Any]]
    df: pd.DataFrame  # clean data incl. lineage columns
    profile: dict[str, Any]
    lineage: dict[str, Any]
    sheet_name: str = ""
    # filled by the builder
    col_index: dict[str, int] = field(default_factory=dict)
    n: int = 0


@dataclass
class WorkbookSource:
    filename: str
    kind: str
    sha256: str
    size: int
    original_bytes: bytes | None = None


def excel_serial(dt: datetime) -> float:
    """Excel (1900 date system) serial number — used for cached formula results."""
    return (dt - datetime(1899, 12, 30)).total_seconds() / 86400.0


def safe_sheet_name(name: str, used: set[str]) -> str:
    s = re.sub(r"[\[\]:*?/\\]", " ", name).strip().strip("'") or "Sheet"
    s = s[:31]
    base, i = s, 2
    while s.lower() in {u.lower() for u in used}:
        suffix = f" ({i})"
        s = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(s)
    return s


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def _rng(sheet: str, col: int, r1: int, r2: int) -> str:
    c = xl_col_to_name(col)
    return f"{_q(sheet)}!${c}${r1 + 1}:${c}${r2 + 1}"


def _measures(ds: WorkbookDataset) -> list[dict[str, Any]]:
    return [
        c for c in ds.columns
        if c["logical_type"] in ("integer", "number", "duration") and c.get("semantic_hint") != "identifier"
    ]


def _categories(ds: WorkbookDataset) -> list[dict[str, Any]]:
    prof = {p["key"]: p for p in ds.profile["columns"]}
    out = []
    for c in ds.columns:
        p = prof.get(c["key"], {})
        if c["logical_type"] in ("text", "boolean") and 1 < p.get("distinct", 0) <= MAX_CATEGORY and c.get("semantic_hint") in ("category", None) and p.get("unique_ratio", 1) < 0.9:
            out.append(c)
    return out


def _dates(ds: WorkbookDataset) -> list[dict[str, Any]]:
    return [c for c in ds.columns if c["logical_type"] in ("date", "datetime")]


class _Styles:
    def __init__(self, wb: xlsxwriter.Workbook):
        self.wb = wb
        self._cache: dict[tuple, Any] = {}

    def get(self, lao: bool = False, **props) -> Any:
        props.setdefault("font_name", LAO_FONT if lao else BASE_FONT)
        props.setdefault("font_size", 10)
        props.setdefault("valign", "vcenter")
        key = tuple(sorted(props.items()))
        if key not in self._cache:
            self._cache[key] = self.wb.add_format(props)
        return self._cache[key]

    def text(self, s: Any, **props) -> Any:
        return self.get(lao=isinstance(s, str) and has_lao(s), **props)


def num_format(col: dict[str, Any]) -> str:
    t, unit = col["logical_type"], col.get("unit")
    if t == "duration":
        return "[h]:mm:ss"
    if t == "date":
        return "yyyy-mm-dd"
    if t == "datetime":
        return "yyyy-mm-dd hh:mm"
    if t in ("integer", "number"):
        if unit == "percent":
            return "0.0%"
        if unit == "LAK":
            return '#,##0 "₭"'
        if unit == "USD":
            return '"$"#,##0.00'
        if unit == "THB":
            return '#,##0.00 "฿"'
        if col.get("semantic_hint") == "identifier":
            return "0"
        return "#,##0" if t == "integer" else "#,##0.00"
    if t in ("text", "phone"):
        return "@"
    return "General"


def _excel_value(col: dict[str, Any], v: Any) -> Any:
    if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and v != v):
        return None
    t = col["logical_type"]
    if t == "duration":
        return float(v) / 86400.0
    if t in ("date", "datetime"):
        return v.to_pydatetime() if isinstance(v, pd.Timestamp) else v
    if t == "boolean":
        return bool(v)
    if t in ("integer",):
        return int(v)
    if t == "number":
        return float(v)
    return str(v)


def _row_flags(ds: WorkbookDataset) -> list[str]:
    flags = [[] for _ in range(ds.n)]
    pos = {int(r): i for i, r in enumerate(ds.df["_source_row"].tolist())}
    for group in ds.profile.get("duplicate_groups", []):
        first = group[0]
        for r in group[1:]:
            if r in pos:
                flags[pos[r]].append(f"duplicate of source row {first}")
    for c in ds.profile["columns"]:
        if c.get("parse_failures"):
            for ex in c.get("failure_examples_all", c.get("failure_examples", [])):
                if ex["row"] in pos:
                    flags[pos[ex["row"]]].append(f"{c['label']}: unreadable '{ex['value']}'")
    return ["; ".join(f) for f in flags]


# ---------------------------------------------------------------------------


def build_workbook(datasets: list[WorkbookDataset], source: WorkbookSource, title: str | None = None) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False, "strings_to_formulas": False, "strings_to_urls": False})
    wb.set_properties({
        "title": title or f"PRISM — {source.filename}",
        "author": "PRISM",
        "comments": f"Generated by PRISM from {source.filename} (sha256 {source.sha256[:16]}…)",
    })
    wb.set_calc_mode("auto")
    st = _Styles(wb)
    used: set[str] = set()
    sheets: dict[str, Any] = {}

    for name in ("README",):
        sheets[name] = wb.add_worksheet(safe_sheet_name(name, used))
    datasets = [d for d in datasets if len(d.df) > 0]
    datasets.sort(key=lambda d: -len(d.df))
    for i, ds in enumerate(datasets):
        if len(ds.df) > EXCEL_MAX_ROWS:
            raise ValueError(f"Dataset '{ds.name}' has {len(ds.df)} rows; Excel supports at most {EXCEL_MAX_ROWS}.")
        ds.sheet_name = safe_sheet_name("Data" if i == 0 else f"Data {i + 1}", used)
        ds.n = len(ds.df)
        sheets[ds.sheet_name] = wb.add_worksheet(ds.sheet_name)
    for name in ("Summary", "Analysis", "Data Quality", "Lookups"):
        sheets[name] = wb.add_worksheet(safe_sheet_name(name, used))
    has_excluded = any(d.lineage.get("excluded_rows") for d in datasets)
    if has_excluded:
        sheets["Excluded Rows"] = wb.add_worksheet(safe_sheet_name("Excluded Rows", used))

    lookups = _write_lookups(wb, st, sheets["Lookups"], datasets)
    for ds in datasets:
        _write_data(wb, st, sheets[ds.sheet_name], ds, lookups)
    _write_summary(wb, st, sheets["Summary"], datasets)
    _write_analysis(wb, st, sheets["Analysis"], datasets)
    _write_quality(wb, st, sheets["Data Quality"], datasets)
    if has_excluded:
        _write_excluded(st, sheets["Excluded Rows"], datasets)
    src_sheets = []
    if source.kind == "xlsx" and source.original_bytes:
        src_sheets = _copy_source_sheets(wb, st, source.original_bytes, used)
    _write_readme(st, sheets["README"], datasets, source, src_sheets, has_excluded)
    sheets["README"].activate()
    wb.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _write_data(wb, st: _Styles, ws, ds: WorkbookDataset, lookups: dict[tuple[str, str], str]) -> None:
    cols = ds.columns
    n = ds.n
    lineage_labels = ["Source sheet", "Source row", "PRISM flags"]
    labels = [c["label"] for c in cols]
    taken = {l.lower() for l in labels}
    lineage_labels = [l if l.lower() not in taken else f"{l} (PRISM)" for l in lineage_labels]
    flags = _row_flags(ds)
    width = len(cols) + 3
    col_fmts = []
    for c in cols:
        lao = bool(c.get("has_lao"))
        col_fmts.append(st.get(lao=lao, num_format=num_format(c)))
    meta_fmt = st.get(font_color=MUTED)
    flag_fmt = st.get(font_color=EMBER)

    for j, c in enumerate(cols):
        ds.col_index[c["key"]] = j
    ds.col_index["_source_row"] = len(cols) + 1

    # rows
    values = {c["key"]: ds.df[c["key"]].tolist() for c in cols}
    sheets_col = ds.df["_source_sheet"].tolist()
    rows_col = ds.df["_source_row"].tolist()
    for i in range(n):
        r = i + 1
        for j, c in enumerate(cols):
            v = _excel_value(c, values[c["key"]][i])
            fmt = col_fmts[j]
            if v is None:
                ws.write_blank(r, j, None, fmt)
            elif isinstance(v, bool):
                ws.write_boolean(r, j, v, fmt)
            elif isinstance(v, (int, float)):
                ws.write_number(r, j, v, fmt)
            elif isinstance(v, datetime):
                ws.write_datetime(r, j, v, fmt)
            else:
                if c.get("has_lao") is False and has_lao(v):
                    ws.write_string(r, j, v, st.get(lao=True, num_format="@"))
                else:
                    ws.write_string(r, j, v, fmt)
        ws.write_string(r, len(cols), str(sheets_col[i]), st.text(sheets_col[i], font_color=MUTED))
        ws.write_number(r, len(cols) + 1, int(rows_col[i]), meta_fmt)
        if flags[i]:
            ws.write_string(r, len(cols) + 2, flags[i], flag_fmt)
        else:
            ws.write_blank(r, len(cols) + 2, None, flag_fmt)

    headers = [{"header": lb, "header_format": st.text(lb, bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True, border=1, border_color=NAVY_2)} for lb in labels + lineage_labels]
    tname = re.sub(r"\W", "_", f"tbl_{ds.sheet_name}", flags=re.ASCII)
    ws.add_table(0, 0, max(n, 1), width - 1, {
        "name": tname, "columns": headers, "style": "Table Style Light 9", "autofilter": True,
    })
    ws.set_row(0, 34)
    ws.freeze_panes(1, 0)

    # widths
    for j, c in enumerate(cols):
        sample = [str(x) for x in ds.df[c["key"]].dropna().head(200).tolist()]
        w = max([len(c["label"]) * 0.9] + [len(s) for s in sample] + [8])
        if c["logical_type"] in ("date",):
            w = 12
        elif c["logical_type"] == "datetime":
            w = 17
        elif c["logical_type"] == "duration":
            w = 11
        ws.set_column(j, j, min(max(w * 1.1, 9), 45))
    ws.set_column(len(cols), len(cols), 14)
    ws.set_column(len(cols) + 1, len(cols) + 1, 10)
    ws.set_column(len(cols) + 2, len(cols) + 2, 40)

    last = max(n, 1)
    # conditional formatting: missing values, flagged rows
    prof = {p["key"]: p for p in ds.profile["columns"]}
    blank_fmt = wb.add_format({"bg_color": EMBER_BG})
    for j, c in enumerate(cols):
        if prof.get(c["key"], {}).get("missing"):
            ws.conditional_format(1, j, last, j, {"type": "blanks", "format": blank_fmt})
    flag_col = xl_col_to_name(len(cols) + 2)
    ws.conditional_format(1, 0, last, width - 1, {
        "type": "formula", "criteria": f'=LEN(${flag_col}2)>0', "format": wb.add_format({"bottom": 1, "bottom_color": EMBER}),
    })

    # data validation for future edits
    for j, c in enumerate(cols):
        key = (ds.sheet_name, c["key"])
        if key in lookups:
            ws.data_validation(1, j, last, j, {
                "validate": "list", "source": lookups[key], "error_type": "warning",
                "error_title": "Unknown value", "error_message": "This value is not in the Lookups list. Keep it anyway?",
                "ignore_blank": True,
            })
        elif c["logical_type"] in ("date", "datetime"):
            ws.data_validation(1, j, last, j, {
                "validate": "date", "criteria": ">=", "value": datetime(1900, 1, 1), "error_type": "warning",
                "error_message": "Please enter a valid date.", "ignore_blank": True,
            })
        elif c["logical_type"] in ("integer", "number", "duration"):
            ws.data_validation(1, j, last, j, {
                "validate": "decimal", "criteria": "between", "minimum": -1e15, "maximum": 1e15,
                "error_type": "warning", "error_message": "Please enter a number.", "ignore_blank": True,
            })


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def _write_lookups(wb, st: _Styles, ws, datasets: list[WorkbookDataset]) -> dict[tuple[str, str], str]:
    ws.write(0, 0, "Lookups — allowed values for category columns (used by Data validation)", st.get(bold=True, font_size=13, font_color=NAVY))
    ws.write(1, 0, "Counts are live formulas (exact, case-sensitive match) over the Data sheet.", st.get(font_color=MUTED, italic=True))
    out: dict[tuple[str, str], str] = {}
    col = 0
    for ds in datasets:
        n = len(ds.df)
        ds_cols = {c["key"]: j for j, c in enumerate(ds.columns)}
        for c in _categories(ds):
            if c["logical_type"] != "text":
                continue
            vc = ds.df[c["key"]].dropna().astype(str).value_counts()
            vals = sorted(vc.index.tolist(), key=lambda v: (-vc[v], v))
            ws.write(3, col, f"{c['label']}", st.text(c["label"], bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True))
            ws.write(3, col + 1, "Count", st.get(bold=True, font_color="#FFFFFF", bg_color=NAVY))
            ws.write(2, col, f"{ds.sheet_name}", st.get(font_color=MUTED))
            data_rng = _rng(ds.sheet_name, ds_cols[c["key"]], 1, max(n, 1))
            for i, v in enumerate(vals):
                r = 4 + i
                ws.write_string(r, col, v, st.text(v))
                ws.write_formula(r, col + 1, f"=SUMPRODUCT(EXACT({data_rng},{xl_rowcol_to_cell(r, col)})*1)", st.get(num_format="#,##0"), int(vc[v]))
            if vals:
                ws.conditional_format(4, col + 1, 3 + len(vals), col + 1, {"type": "data_bar", "bar_color": "#7FD8BE", "bar_solid": True})
                out[(ds.sheet_name, c["key"])] = f"=Lookups!${xl_col_to_name(col)}$5:${xl_col_to_name(col)}${4 + len(vals)}"
            ws.set_column(col, col, min(max(len(c["label"]), max((len(v) for v in vals), default=8)) + 4, 40))
            ws.set_column(col + 1, col + 1, 9)
            col += 3
    if col == 0:
        ws.write(3, 0, "No category columns were detected.", st.get(font_color=MUTED))
    ws.freeze_panes(4, 0)
    return out


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _agg_value(series: pd.Series, fn: str) -> float | None:
    s = series.dropna().astype("float64")
    if s.empty:
        return 0 if fn in ("SUM", "COUNT") else None
    return {"SUM": s.sum(), "AVERAGE": s.mean(), "MIN": s.min(), "MAX": s.max(), "COUNT": float(len(s))}[fn]


def _check(ws, st, r: int, c_formula: int, c_value: int, c_out: int) -> None:
    a, b = xl_rowcol_to_cell(r, c_formula), xl_rowcol_to_cell(r, c_value)
    ws.write_formula(r, c_out, f'=IF(ISBLANK({b}),"",IF(ABS(N({a})-N({b}))<=0.000001*MAX(1,ABS(N({b}))),"✓","≠ changed"))', st.get(font_color=AURORA, align="center"), "✓")


def _write_summary(wb, st: _Styles, ws, datasets: list[WorkbookDataset]) -> None:
    ws.set_column(0, 0, 34)
    ws.set_column(1, 4, 16)
    ws.set_column(5, 5, 4)
    ws.set_column(6, 9, 14)
    ws.write(0, 0, "Summary", st.get(bold=True, font_size=16, font_color=NAVY))
    ws.write(1, 0, "Live formulas over the Data sheet(s). 'PRISM value' is the deterministic result computed at generation time; ✓ confirms the formula agrees.", st.get(font_color=MUTED, italic=True))
    hdr = st.get(bold=True, font_color="#FFFFFF", bg_color=NAVY)
    r = 3
    for ds in datasets:
        n = ds.n
        ws.write(r, 0, ds.name, st.text(ds.name, bold=True, font_size=13, font_color=NAVY))
        ws.write(r, 1, f"→ sheet '{ds.sheet_name}'", st.get(font_color=MUTED))
        r += 1
        for j, h in enumerate(["Metric", "Value (formula)", "PRISM value", "Check"]):
            ws.write(r, j, h, hdr)
        r += 1
        src_rng = _rng(ds.sheet_name, ds.col_index["_source_row"], 1, max(n, 1))
        ws.write(r, 0, "Records", st.get(bold=True))
        ws.write_formula(r, 1, f"=COUNT({src_rng})", st.get(num_format="#,##0", bold=True), n)
        ws.write_number(r, 2, n, st.get(num_format="#,##0", font_color=MUTED))
        _check(ws, st, r, 1, 2, 3)
        r += 1
        for c in _dates(ds):
            rng = _rng(ds.sheet_name, ds.col_index[c["key"]], 1, max(n, 1))
            s = ds.df[c["key"]].dropna()
            for fn, label in (("MIN", "earliest"), ("MAX", "latest")):
                v = (s.min() if fn == "MIN" else s.max()) if not s.empty else None
                ws.write(r, 0, f"{c['label']} — {label}", st.text(c["label"]))
                fmt = st.get(num_format=num_format(c))
                if v is not None:
                    ws.write_formula(r, 1, f"={fn}({rng})", fmt, excel_serial(v.to_pydatetime()))
                    ws.write_datetime(r, 2, v.to_pydatetime(), st.get(num_format=num_format(c), font_color=MUTED))
                else:
                    ws.write_formula(r, 1, f"={fn}({rng})", fmt)
                _check(ws, st, r, 1, 2, 3)
                r += 1
        for c in _measures(ds):
            rng = _rng(ds.sheet_name, ds.col_index[c["key"]], 1, max(n, 1))
            series = ds.df[c["key"]]
            scale = 86400.0 if c["logical_type"] == "duration" else 1.0
            for fn, label in (("SUM", "total"), ("AVERAGE", "average"), ("MIN", "minimum"), ("MAX", "maximum")):
                v = _agg_value(series, fn)
                fmt = st.get(num_format=num_format(c) if not (fn == "AVERAGE" and c["logical_type"] == "integer" and c.get("unit") is None) else "#,##0.00")
                ws.write(r, 0, f"{c['label']} — {label}", st.text(c["label"]))
                if v is None:
                    ws.write_formula(r, 1, f'=IFERROR({fn}({rng}),"")', fmt, "")
                else:
                    ws.write_formula(r, 1, f"={fn}({rng})", fmt, v / scale)
                    ws.write_number(r, 2, v / scale, st.get(num_format=fmt_num(c, fn), font_color=MUTED))
                _check(ws, st, r, 1, 2, 3)
                r += 1
        # category breakdowns
        measures = _measures(ds)
        primary = measures[0] if measures else None
        for c in _categories(ds)[:4]:
            r += 1
            ws.write(r, 0, f"By {c['label']}", st.text(c["label"], bold=True, font_color=NAVY, font_size=11))
            r += 1
            heads = [c["label"], "Records", "Share"]
            if primary:
                heads.append(f"{primary['label']} (total)")
            for j, h in enumerate(heads):
                ws.write(r, j, h, st.text(h, bold=True, font_color="#FFFFFF", bg_color=NAVY_2, text_wrap=True))
            r += 1
            cat_rng = _rng(ds.sheet_name, ds.col_index[c["key"]], 1, max(n, 1))
            series = ds.df[c["key"]]
            if c["logical_type"] == "boolean":
                vc = series.dropna().astype(bool).value_counts()
            else:
                vc = series.dropna().astype(str).value_counts()
            start = r
            for v, cnt in vc.items():
                if c["logical_type"] == "boolean":
                    ws.write_boolean(r, 0, bool(v), st.get())
                    crit = f"({cat_rng}={xl_rowcol_to_cell(r, 0)})"
                else:
                    ws.write_string(r, 0, str(v), st.text(str(v)))
                    crit = f"EXACT({cat_rng},{xl_rowcol_to_cell(r, 0)})"
                ws.write_formula(r, 1, f"=SUMPRODUCT({crit}*1)", st.get(num_format="#,##0"), int(cnt))
                ws.write_formula(r, 2, f"=IFERROR({xl_rowcol_to_cell(r, 1)}/COUNT({src_rng}),0)", st.get(num_format="0.0%"), cnt / n if n else 0)
                if primary:
                    mrng = _rng(ds.sheet_name, ds.col_index[primary["key"]], 1, max(n, 1))
                    scale = 86400.0 if primary["logical_type"] == "duration" else 1.0
                    mask = (series.astype(str) == str(v)) if c["logical_type"] != "boolean" else (series == v)
                    msum = float(ds.df.loc[mask.fillna(False), primary["key"]].dropna().astype("float64").sum())
                    ws.write_formula(r, 3, f"=SUMPRODUCT({crit}*{mrng})", st.get(num_format=num_format(primary)), msum / scale)
                r += 1
            if r > start:
                ws.conditional_format(start, 1, r - 1, 1, {"type": "data_bar", "bar_color": "#7FD8BE", "bar_solid": True})
                if len(vc) >= 2:
                    chart = wb.add_chart({"type": "bar"})
                    chart.add_series({
                        "name": f"Records by {c['label']}",
                        "categories": [ws.name, start, 0, r - 1, 0],
                        "values": [ws.name, start, 1, r - 1, 1],
                        "fill": {"color": "#2FB58A"}, "gap": 60,
                    })
                    chart.set_title({"name": f"Records by {c['label']}", "name_font": {"size": 11, "name": LAO_FONT}})
                    chart.set_legend({"none": True})
                    chart.set_y_axis({"reverse": True, "num_font": {"name": LAO_FONT, "size": 9}})
                    chart.set_size({"width": 480, "height": max(220, 26 * len(vc) + 80)})
                    ws.insert_chart(start - 1, 6, chart)
            r = max(r, start + (max(220, 26 * len(vc) + 80) // 20) if len(vc) >= 2 else r)
        r += 2


def fmt_num(c: dict[str, Any], fn: str) -> str:
    if fn == "AVERAGE" and c["logical_type"] == "integer" and c.get("unit") is None:
        return "#,##0.00"
    return num_format(c)


# ---------------------------------------------------------------------------
# Analysis (monthly trends, cross-tabs)
# ---------------------------------------------------------------------------


def _write_analysis(wb, st: _Styles, ws, datasets: list[WorkbookDataset]) -> None:
    ws.set_column(0, 0, 18)
    ws.set_column(1, 12, 15)
    ws.write(0, 0, "Analysis", st.get(bold=True, font_size=16, font_color=NAVY))
    ws.write(1, 0, "Monthly trends and cross-tabulations computed with live COUNTIFS / SUMIFS formulas.", st.get(font_color=MUTED, italic=True))
    r = 3
    wrote = False
    for ds in datasets:
        n = ds.n
        dates = _dates(ds)
        measures = _measures(ds)[:3]
        if dates:
            d = dates[0]
            s = ds.df[d["key"]].dropna()
            if not s.empty:
                wrote = True
                months = pd.period_range(s.min().to_period("M"), s.max().to_period("M"), freq="M")
                if len(months) > 120:
                    months = months[-120:]
                ws.write(r, 0, f"{ds.name} — monthly by {d['label']}", st.text(ds.name, bold=True, font_size=12, font_color=NAVY))
                r += 1
                heads = ["Month", "Records"] + [f"{m['label']} (total)" for m in measures]
                for j, h in enumerate(heads):
                    ws.write(r, j, h, st.text(h, bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True))
                ws.set_row(r, 30)
                r += 1
                drng = _rng(ds.sheet_name, ds.col_index[d["key"]], 1, max(n, 1))
                start = r
                per = ds.df[d["key"]].dt.to_period("M")
                for m in months:
                    first = m.to_timestamp().to_pydatetime()
                    a = xl_rowcol_to_cell(r, 0)
                    ws.write_datetime(r, 0, first, st.get(num_format="mmm yyyy"))
                    cnt = int((per == m).sum())
                    ws.write_formula(r, 1, f'=COUNTIFS({drng},">="&{a},{drng},"<"&EDATE({a},1))', st.get(num_format="#,##0"), cnt)
                    for k, mc in enumerate(measures):
                        mrng = _rng(ds.sheet_name, ds.col_index[mc["key"]], 1, max(n, 1))
                        scale = 86400.0 if mc["logical_type"] == "duration" else 1.0
                        v = float(ds.df.loc[(per == m).fillna(False), mc["key"]].dropna().astype("float64").sum()) / scale
                        ws.write_formula(r, 2 + k, f'=SUMIFS({mrng},{drng},">="&{a},{drng},"<"&EDATE({a},1))', st.get(num_format=num_format(mc)), v)
                    r += 1
                if r - start >= 2:
                    chart = wb.add_chart({"type": "column"})
                    chart.add_series({
                        "name": "Records", "categories": [ws.name, start, 0, r - 1, 0],
                        "values": [ws.name, start, 1, r - 1, 1], "fill": {"color": "#2FB58A"},
                    })
                    if measures:
                        line = wb.add_chart({"type": "line"})
                        line.add_series({
                            "name": f"{measures[0]['label']} (total)", "categories": [ws.name, start, 0, r - 1, 0],
                            "values": [ws.name, start, 2, r - 1, 2], "y2_axis": True,
                            "line": {"color": "#6A4FD1", "width": 2.25}, "marker": {"type": "circle"},
                        })
                        chart.combine(line)
                    chart.set_title({"name": f"Monthly activity — {ds.name}"[:80], "name_font": {"size": 11, "name": LAO_FONT}})
                    chart.set_legend({"position": "bottom", "font": {"name": LAO_FONT}})
                    chart.set_x_axis({"num_format": "mmm yy", "num_font": {"rotation": -45}})
                    chart.set_size({"width": 640, "height": 300})
                    ws.insert_chart(start - 2, 2 + len(measures) + 1, chart)
                r = max(r, start + 16) + 1
        cats = _categories(ds)
        if len(cats) >= 2:
            c1, c2 = cats[0], cats[1]
            v1 = ds.df[c1["key"]].dropna().astype(str).value_counts().index.tolist()[:15]
            v2 = ds.df[c2["key"]].dropna().astype(str).value_counts().index.tolist()[:10]
            if c1["logical_type"] == "text" and c2["logical_type"] == "text" and v1 and v2:
                wrote = True
                ws.write(r, 0, f"{ds.name} — {c1['label']} × {c2['label']} (records)", st.text(c1["label"] + c2["label"], bold=True, font_size=12, font_color=NAVY))
                r += 1
                ws.write(r, 0, f"{c1['label']} \\ {c2['label']}", st.text(c1["label"], bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True))
                for j, b in enumerate(v2):
                    ws.write(r, 1 + j, b, st.text(b, bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True))
                ws.write(r, 1 + len(v2), "Total", st.get(bold=True, font_color="#FFFFFF", bg_color=NAVY))
                hr = r
                r += 1
                rng1 = _rng(ds.sheet_name, ds.col_index[c1["key"]], 1, max(n, 1))
                rng2 = _rng(ds.sheet_name, ds.col_index[c2["key"]], 1, max(n, 1))
                s1, s2 = ds.df[c1["key"]].astype(str), ds.df[c2["key"]].astype(str)
                start = r
                for a in v1:
                    ws.write_string(r, 0, a, st.text(a, bold=True))
                    for j, b in enumerate(v2):
                        cnt = int(((s1 == a) & (s2 == b)).sum())
                        ws.write_formula(
                            r, 1 + j,
                            f"=SUMPRODUCT(EXACT({rng1},{xl_rowcol_to_cell(r, 0)})*EXACT({rng2},{xl_rowcol_to_cell(hr, 1 + j, col_abs=False, row_abs=True)}))",
                            st.get(num_format="#,##0"), cnt,
                        )
                    tot = int(sum(((s1 == a) & (s2 == b)).sum() for b in v2))
                    ws.write_formula(r, 1 + len(v2), f"=SUM({xl_rowcol_to_cell(r, 1)}:{xl_rowcol_to_cell(r, len(v2))})", st.get(num_format="#,##0", bold=True), tot)
                    r += 1
                ws.conditional_format(start, 1, r - 1, len(v2), {"type": "2_color_scale", "min_color": "#FFFFFF", "max_color": "#6FD3B3"})
                r += 2
    if not wrote:
        ws.write(r, 0, "No date or category columns suitable for trend/cross-tab analysis were found.", st.get(font_color=MUTED))


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def _write_quality(wb, st: _Styles, ws, datasets: list[WorkbookDataset]) -> None:
    ws.set_column(0, 0, 30)
    ws.set_column(1, 8, 14)
    ws.set_column(9, 9, 70)
    ws.write(0, 0, "Data Quality", st.get(bold=True, font_size=16, font_color=NAVY))
    ws.write(1, 0, "Deterministic profiling results. Original values are never discarded: unreadable values stay in the source and are flagged in the Data sheet's 'PRISM flags' column.", st.get(font_color=MUTED, italic=True))
    hdr = st.get(bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True)
    r = 3
    for ds in datasets:
        p = ds.profile
        n = ds.n
        ws.write(r, 0, ds.name, st.text(ds.name, bold=True, font_size=13, font_color=NAVY))
        r += 1
        score_fmt = st.get(bold=True, font_size=20, font_color=AURORA if p["quality_score"] >= 85 else EMBER, num_format="0.0")
        ws.write(r, 0, "Quality score (0–100)", st.get(bold=True))
        ws.write_number(r, 1, p["quality_score"], score_fmt)
        ws.write(r, 2, "Completeness", st.get(bold=True))
        ws.write_number(r, 3, p["completeness_pct"] / 100, st.get(num_format="0.0%", bold=True))
        ws.write(r, 4, "Duplicate rows", st.get(bold=True))
        ws.write_number(r, 5, p["duplicate_rows"], st.get(bold=True, font_color=EMBER if p["duplicate_rows"] else INK))
        r += 1
        pen = "; ".join(f"{k.replace('_', ' ')} −{v}" for k, v in p["quality_penalties"].items() if v)
        ws.write(r, 0, "Score deductions: " + (pen or "none"), st.get(font_color=MUTED, italic=True))
        r += 2
        heads = ["Column", "Type", "Unit", "Filled", "Missing", "Completeness", "Distinct", "Unreadable", "Outliers", "Notes"]
        for j, h in enumerate(heads):
            ws.write(r, j, h, hdr)
        r += 1
        start = r
        prof = {c["key"]: c for c in p["columns"]}
        for c in ds.columns:
            pc = prof[c["key"]]
            j = ds.col_index[c["key"]]
            rng = _rng(ds.sheet_name, j, 1, max(n, 1))
            ws.write(r, 0, c["label"], st.text(c["label"], bold=True))
            ws.write(r, 1, c["logical_type"], st.get(font_color=NEBULA if c.get("semantic_hint") else INK))
            ws.write(r, 2, c.get("unit") or "", st.get(font_color=MUTED))
            filled = pc["non_null"]
            ws.write_formula(r, 3, f"=COUNTA({rng})", st.get(num_format="#,##0"), filled)
            ws.write_formula(r, 4, f"=ROWS({rng})-{xl_rowcol_to_cell(r, 3)}", st.get(num_format="#,##0"), n - filled)
            ws.write_formula(r, 5, f"=IFERROR({xl_rowcol_to_cell(r, 3)}/ROWS({rng}),0)", st.get(num_format="0.0%"), filled / n if n else 0)
            ws.write_number(r, 6, pc["distinct"], st.get(num_format="#,##0"))
            ws.write_number(r, 7, pc["parse_failures"], st.get(num_format="#,##0", font_color=EMBER if pc["parse_failures"] else INK))
            ws.write_number(r, 8, pc.get("outliers") or 0, st.get(num_format="#,##0"))
            notes = "; ".join(c.get("notes") or [])
            ws.write(r, 9, notes, st.text(notes, font_color=MUTED, text_wrap=False))
            r += 1
        ws.conditional_format(start, 5, r - 1, 5, {"type": "data_bar", "bar_color": "#7FD8BE", "bar_solid": True, "min_type": "num", "min_value": 0, "max_type": "num", "max_value": 1})
        ws.conditional_format(start, 7, r - 1, 7, {"type": "cell", "criteria": ">", "value": 0, "format": wb.add_format({"bg_color": EMBER_BG})})

        # total checks (formula-based reconciliation with the source's own total rows)
        if p.get("total_checks"):
            r += 1
            ws.write(r, 0, "Source total rows reconciled", st.get(bold=True, font_size=11, font_color=NAVY))
            r += 1
            for j, h in enumerate(["Column", "Source row", "Stated total", "Computed (formula)", "Difference", "Result"]):
                ws.write(r, j, h, hdr)
            r += 1
            src_rng = _rng(ds.sheet_name, ds.col_index["_source_row"], 1, max(n, 1))
            totals_rows = sorted({tc["source_row"] for tc in p["total_checks"]})
            for tc in p["total_checks"]:
                c = next(c for c in ds.columns if c["key"] == tc["column"])
                mrng = _rng(ds.sheet_name, ds.col_index[c["key"]], 1, max(n, 1))
                idx = totals_rows.index(tc["source_row"])
                lo = totals_rows[idx - 1] if idx > 0 else 0
                seg = abs(tc["computed"] - _seg_sum(ds, c["key"], lo, tc["source_row"])) < 1e-6
                lo_used = lo if seg else 0
                ws.write(r, 0, tc["label"], st.text(tc["label"]))
                ws.write_number(r, 1, tc["source_row"])
                ws.write_number(r, 2, tc["stated"], st.get(num_format=num_format(c)))
                ws.write_formula(r, 3, f'=SUMIFS({mrng},{src_rng},">"&{lo_used},{src_rng},"<"&{xl_rowcol_to_cell(r, 1)})', st.get(num_format=num_format(c)), tc["computed"])
                ws.write_formula(r, 4, f"={xl_rowcol_to_cell(r, 2)}-{xl_rowcol_to_cell(r, 3)}", st.get(num_format=num_format(c)), tc["difference"])
                ws.write_formula(r, 5, f'=IF(ABS({xl_rowcol_to_cell(r, 4)})<0.005,"✓ matches","✗ differs")', st.get(bold=True), "✓ matches" if tc["matches"] else "✗ differs")
                r += 1
            ws.conditional_format(r - len(p["total_checks"]), 5, r - 1, 5, {"type": "text", "criteria": "begins with", "value": "✗", "format": wb.add_format({"bg_color": FLARE_BG, "font_color": "#B00020"})})

        # issues
        r += 1
        ws.write(r, 0, "Issues", st.get(bold=True, font_size=11, font_color=NAVY))
        r += 1
        for j, h in enumerate(["Severity", "Issue", "Column", "Count", "Details"]):
            ws.write(r, j if j < 4 else 9, h, hdr)
        for j in range(4, 9):
            ws.write_blank(r, j, None, hdr)
        r += 1
        istart = r
        labels = {c["key"]: c["label"] for c in ds.columns}
        for iss in p["issues"]:
            ws.write(r, 0, iss["severity"].upper(), st.get(bold=True))
            ws.write(r, 1, iss["code"].replace("_", " "))
            colname = labels.get(iss.get("column"), "") if iss.get("column") else ""
            ws.write(r, 2, colname, st.text(colname))
            ws.write_number(r, 3, iss.get("count") or 0, st.get(num_format="#,##0"))
            ws.write(r, 9, iss["message"], st.text(iss["message"]))
            r += 1
        if not p["issues"]:
            ws.write(r, 0, "No issues found.", st.get(font_color=AURORA, bold=True))
            r += 1
        else:
            for sev, color in (("ERROR", FLARE_BG), ("WARNING", EMBER_BG), ("INFO", "#EEF2F8")):
                ws.conditional_format(istart, 0, r - 1, 0, {"type": "cell", "criteria": "==", "value": f'"{sev}"', "format": wb.add_format({"bg_color": color})})
        r += 3


def _seg_sum(ds: WorkbookDataset, key: str, lo: int, hi: int) -> float:
    m = (ds.df["_source_row"] > lo) & (ds.df["_source_row"] < hi)
    return float(ds.df.loc[m.fillna(False), key].dropna().astype("float64").sum())


# ---------------------------------------------------------------------------
# Excluded rows
# ---------------------------------------------------------------------------


def _write_excluded(st: _Styles, ws, datasets: list[WorkbookDataset]) -> None:
    ws.set_column(0, 0, 28)
    ws.set_column(1, 1, 11)
    ws.set_column(2, 2, 30)
    ws.set_column(3, 3, 90)
    ws.write(0, 0, "Excluded Rows", st.get(bold=True, font_size=16, font_color=NAVY))
    ws.write(1, 0, "Rows of the source that are not data records. They are listed here so no source information is lost.", st.get(font_color=MUTED, italic=True))
    hdr = st.get(bold=True, font_color="#FFFFFF", bg_color=NAVY)
    for j, h in enumerate(["Dataset", "Source row", "Reason", "Original values"]):
        ws.write(3, j, h, hdr)
    r = 4
    for ds in datasets:
        for e in ds.lineage.get("excluded_rows", []):
            vals = " | ".join(f"{k}: {v}" for k, v in (e.get("values") or {}).items() if v not in (None, ""))
            ws.write(r, 0, ds.name, st.text(ds.name))
            ws.write_number(r, 1, e["source_row"])
            ws.write(r, 2, e["reason"])
            ws.write(r, 3, vals, st.text(vals))
            r += 1


# ---------------------------------------------------------------------------
# Source sheet copies
# ---------------------------------------------------------------------------


def _hex(color) -> str | None:
    try:
        if color is None or color.type != "rgb" or not isinstance(color.rgb, str):
            return None
        return "#" + color.rgb[-6:]
    except Exception:
        return None


def _copy_source_sheets(wb, st: _Styles, data: bytes, used: set[str]) -> list[str]:
    import openpyxl

    src = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    names = []
    style_cache: dict[tuple, Any] = {}
    for sws in src.worksheets:
        if sws.max_row * sws.max_column > 2_000_000:
            continue  # very large sheets are represented by the Data sheet only
        name = safe_sheet_name(f"Src-{sws.title}", used)
        ws = wb.add_worksheet(name)
        ws.set_tab_color("#8D99B3")
        names.append(name)
        merged_anchor: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        merged_skip: set[tuple[int, int]] = set()
        for m in sws.merged_cells.ranges:
            merged_anchor[(m.min_row - 1, m.min_col - 1)] = (m.min_row - 1, m.min_col - 1, m.max_row - 1, m.max_col - 1)
            for rr in range(m.min_row - 1, m.max_row):
                for cc in range(m.min_col - 1, m.max_col):
                    if (rr, cc) != (m.min_row - 1, m.min_col - 1):
                        merged_skip.add((rr, cc))
        for row in sws.iter_rows():
            for cell in row:
                r, c = cell.row - 1, cell.column - 1
                if (r, c) in merged_skip:
                    continue
                v = cell.value
                f = cell.font
                fill = cell.fill
                key = (
                    bool(f.b), bool(f.i), f.sz, _hex(f.color),
                    _hex(fill.fgColor) if fill and fill.fill_type == "solid" else None,
                    cell.alignment.horizontal, bool(cell.alignment.wrap_text), cell.number_format,
                    isinstance(v, str) and has_lao(v), bool(cell.border.bottom.style),
                )
                if key not in style_cache:
                    props: dict[str, Any] = {"font_name": LAO_FONT if key[8] else BASE_FONT, "font_size": float(f.sz or 10)}
                    if key[0]:
                        props["bold"] = True
                    if key[1]:
                        props["italic"] = True
                    if key[3]:
                        props["font_color"] = key[3]
                    if key[4] and key[4] != "#000000":
                        props["bg_color"] = key[4]
                    if key[5] in ("left", "center", "right", "fill", "justify", "centerContinuous", "distributed"):
                        props["align"] = {"centerContinuous": "center_across", "distributed": "distributed"}.get(key[5], key[5])
                    if key[6]:
                        props["text_wrap"] = True
                    if key[7] and key[7] != "General":
                        props["num_format"] = key[7]
                    if key[9]:
                        props["border"] = 1
                    style_cache[key] = wb.add_format(props)
                fmt = style_cache[key]
                if (r, c) in merged_anchor:
                    r1, c1, r2, c2 = merged_anchor[(r, c)]
                    if isinstance(v, datetime):
                        ws.merge_range(r1, c1, r2, c2, "", fmt)
                        ws.write_datetime(r1, c1, v, fmt)
                    else:
                        ws.merge_range(r1, c1, r2, c2, "" if v is None else v if not isinstance(v, (int, float)) else "", fmt)
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            ws.write_number(r1, c1, v, fmt)
                    continue
                if v is None:
                    if key[4] or key[9]:
                        ws.write_blank(r, c, None, fmt)
                    continue
                if isinstance(v, bool):
                    ws.write_boolean(r, c, v, fmt)
                elif isinstance(v, (int, float)):
                    ws.write_number(r, c, v, fmt)
                elif isinstance(v, datetime):
                    ws.write_datetime(r, c, v, fmt)
                elif hasattr(v, "isoformat") and not isinstance(v, str):
                    ws.write_string(r, c, v.isoformat(), fmt)
                else:
                    ws.write_string(r, c, str(v), fmt)
        for letter, dim in sws.column_dimensions.items():
            try:
                from openpyxl.utils import column_index_from_string

                idx = column_index_from_string(letter) - 1
                if dim.width:
                    ws.set_column(idx, idx, dim.width)
            except ValueError:
                pass
        for ridx, dim in sws.row_dimensions.items():
            if dim.height:
                ws.set_row(ridx - 1, dim.height)
    return names


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------


def _write_readme(st: _Styles, ws, datasets: list[WorkbookDataset], source: WorkbookSource, src_sheets: list[str], has_excluded: bool) -> None:
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 3)
    ws.set_column(1, 1, 26)
    ws.set_column(2, 2, 100)
    now = datetime.now(TZ)
    r = 1
    ws.write(r, 1, "PRISM", st.get(bold=True, font_size=22, font_color=NAVY))
    r += 1
    ws.write(r, 1, "Data intelligence workbook", st.get(font_size=12, font_color=MUTED))
    r += 2
    kv = st.get(bold=True, font_color=INK)
    for k, v in (
        ("Source file", source.filename),
        ("Source type", source.kind.upper()),
        ("Source fingerprint (SHA-256)", source.sha256),
        ("Generated", now.strftime("%Y-%m-%d %H:%M") + " (Asia/Vientiane)"),
        ("Datasets", str(len(datasets))),
    ):
        ws.write(r, 1, k, kv)
        ws.write(r, 2, v, st.text(v))
        r += 1
    r += 1
    ws.write(r, 1, "Sheets", st.get(bold=True, font_size=13, font_color=NAVY))
    r += 1
    guide = []
    for ds in datasets:
        guide.append((ds.sheet_name, f"{ds.name}: {ds.n:,} records × {len(ds.columns)} columns. Typed, cleaned data in an Excel Table with filters, validation and lineage columns (Source sheet, Source row)."))
    guide += [
        ("Summary", "Key figures as live formulas, each checked against PRISM's independent computation."),
        ("Analysis", "Monthly trends and cross-tabulations (COUNTIFS / SUMIFS), with charts."),
        ("Data Quality", "Quality score, column profiles, reconciliation with source totals, and issues."),
        ("Lookups", "Allowed values for category columns; used by drop-down validation in the Data sheet."),
    ]
    if has_excluded:
        guide.append(("Excluded Rows", "Titles, totals, notes and blank rows from the source that are not data records."))
    for s in src_sheets:
        guide.append((s, "Copy of the original worksheet (values and formatting) for reference."))
    for name, desc in guide:
        ws.write_url(r, 1, f"internal:{_q(name)}!A1", st.text(name, font_color="#1D5FD1", underline=1), string=name)
        ws.write(r, 2, desc, st.text(desc, text_wrap=True))
        r += 1
    r += 1
    ws.write(r, 1, "Transformations", st.get(bold=True, font_size=13, font_color=NAVY))
    r += 1
    for ds in datasets:
        lin = ds.lineage
        lines = [f"Header row(s): {', '.join(map(str, lin.get('header_rows') or [])) or 'none (generic names assigned)'}; data rows {lin.get('first_row')}–{lin.get('last_row')} of sheet '{lin.get('sheet')}'."]
        lines += lin.get("notes", [])
        for c in ds.columns:
            for note in c.get("notes") or []:
                lines.append(f"{c['label']}: {note}")
            if c["logical_type"] == "duration":
                lines.append(f"{c['label']}: durations stored as Excel time values (format [h]:mm:ss).")
        ex = lin.get("excluded_rows") or []
        if ex:
            lines.append(f"{len(ex)} non-data row(s) excluded and listed in 'Excluded Rows'.")
        ws.write(r, 1, ds.name, st.text(ds.name, bold=True))
        for ln in lines:
            ws.write(r, 2, "• " + ln, st.text(ln, text_wrap=True))
            r += 1
        r += 1
    ws.write(r, 1, "Principles", st.get(bold=True, font_size=13, font_color=NAVY))
    r += 1
    for ln in (
        "No records were invented. Values that could not be read are left blank in the typed Data sheet and flagged; originals remain in the source.",
        "All numbers are computed deterministically (formulas in this workbook and PRISM's engine) — never by AI.",
        "Dates use day-first order unless the data proves otherwise; Buddhist Era years are converted to Gregorian.",
    ):
        ws.write(r, 2, "• " + ln, st.text(ln, text_wrap=True))
        r += 1
