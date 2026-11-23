"""Table, title, header and footer detection on RawGrids.

Handles: title rows above tables, multiple tables per sheet (stacked or side by side),
blank rows inside tables, single- and multi-row (merged) headers, repeated header rows,
total/subtotal rows and trailing note rows. Nothing is discarded silently: every row that
is not data is returned in ``excluded`` or ``notes`` with a reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from openpyxl.utils import get_column_letter

from app.ingestion.parsers import (
    is_null,
    normalize_text,
    parse_date,
    parse_duration,
    parse_number,
)
from app.ingestion.readers import RawGrid

TOTAL_RE = re.compile(
    r"^(grand\s*total|sub[\s-]?total|total|totals|sum|ລວມ|ລວມທັງໝົດ|ຍອດລວມ|ລວມຍອດ|ລວມທັງສິ້ນ)(?![a-z])",
    re.IGNORECASE,
)


@dataclass
class ExcludedRow:
    source_row: int
    reason: str
    values: dict[str, str | None]  # source column letter -> value


@dataclass
class DetectedTable:
    sheet: str
    index: int
    origin: str
    labels: list[str]
    original_labels: list[str | None]
    rows: list[list[Any]]
    source_rows: list[int]  # 1-based original row numbers
    source_cols: list[int]  # 1-based original column numbers
    formats: list[list[str | None]] | None
    header_rows: list[int] = field(default_factory=list)
    title: str | None = None
    excluded: list[ExcludedRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    merged_filled: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        if self.title:
            return self.title[:80]
        return self.sheet if self.index == 0 else f"{self.sheet} ({self.index + 1})"

    @property
    def column_letters(self) -> list[str]:
        return [get_column_letter(c) for c in self.source_cols]


# ---------------------------------------------------------------------------
# cell classification
# ---------------------------------------------------------------------------


def cell_kind(v: Any) -> str:
    if v is None or is_null(v):
        return "empty"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, (datetime, date, time, timedelta)):
        return "date"
    s = normalize_text(v)
    if s is None:
        return "empty"
    if isinstance(parse_number(s), (int, float)):
        return "num"
    if isinstance(parse_date(s), datetime):
        return "date"
    if isinstance(parse_duration(s), int):
        return "num"
    return "text"


def _row_stats(row: list[Any]) -> tuple[int, int, list[str]]:
    kinds = [cell_kind(v) for v in row]
    nonempty = sum(k != "empty" for k in kinds)
    text = sum(k == "text" for k in kinds)
    return nonempty, text, kinds


# ---------------------------------------------------------------------------
# region segmentation
# ---------------------------------------------------------------------------


@dataclass
class _Region:
    r0: int
    r1: int  # inclusive
    c0: int
    c1: int  # inclusive


def _segments(mask: list[bool]) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def _regions(grid: RawGrid, empty: list[list[bool]]) -> list[_Region]:
    row_has = [not all(r) for r in empty]
    regions: list[_Region] = []
    for r0, r1 in _segments(row_has):
        col_has = [any(not empty[r][c] for r in range(r0, r1 + 1)) for c in range(grid.n_cols)]
        segs = _segments(col_has)
        # merge segments separated by a single blank column unless they look like separate tables
        merged: list[tuple[int, int]] = []
        for s in segs:
            if merged:
                p = merged[-1]
                gap = s[0] - p[1] - 1
                if gap == 1:
                    def extent(c0, c1):
                        rows = [r for r in range(r0, r1 + 1) if any(not empty[r][c] for c in range(c0, c1 + 1))]
                        return rows[0], rows[-1]

                    if extent(*p) == extent(*s):
                        merged[-1] = (p[0], s[1])
                        continue
            merged.append(s)
        for c0, c1 in merged:
            rows = [r for r in range(r0, r1 + 1) if any(not empty[r][c] for c in range(c0, c1 + 1))]
            regions.append(_Region(rows[0], rows[-1], c0, c1))
    regions.sort(key=lambda g: (g.c0 // 1000, g.r0, g.c0))
    return regions


# ---------------------------------------------------------------------------
# header detection
# ---------------------------------------------------------------------------


def _is_headerish(row: list[Any], width: int) -> bool:
    nonempty, text, _ = _row_stats(row)
    if nonempty == 0:
        return False
    texts = [normalize_text(v) for v in row if normalize_text(v)]
    uniq = len(set(texts)) / max(len(texts), 1)
    fill = nonempty / width
    return text / nonempty >= 0.75 and (fill >= 0.5 or (width <= 3 and nonempty >= 1)) and uniq >= 0.6


def _typed_below(rows: list[list[Any]], h: int, width: int) -> float:
    """Fraction of columns whose values below row h are mostly numeric/date while the
    header cell is text — the strongest header signal."""
    sample = rows[h + 1 : h + 31]
    if not sample:
        return 0.0
    hits = 0
    for c in range(width):
        hk = cell_kind(rows[h][c])
        kinds = [cell_kind(r[c]) for r in sample]
        filled = [k for k in kinds if k != "empty"]
        if hk == "text" and filled and sum(k in ("num", "date", "bool") for k in filled) / len(filled) >= 0.6:
            hits += 1
    return hits / width


def _is_sparse_title(row: list[Any], width: int) -> bool:
    vals = [normalize_text(v) for v in row if normalize_text(v)]
    return len(set(vals)) == 1 and width >= 2 or (len(vals) <= 1)


def detect_header(rows: list[list[Any]], width: int, merged_rows: set[int], force_first: bool = False) -> tuple[list[int], list[int]]:
    """Return (header_row_indices, title_row_indices) relative to the region."""
    if not rows:
        return [], []
    if force_first:
        return [0], []
    limit = min(len(rows), 15)
    best, best_score = None, -1.0
    for i in range(limit):
        if not _is_headerish(rows[i], width):
            continue
        if _is_sparse_title(rows[i], width) and width > 2:
            continue
        nonempty, text, _ = _row_stats(rows[i])
        typed = _typed_below(rows, i, width)
        score = 1.0 + nonempty / width + 2.5 * typed - 0.04 * i
        if score > best_score:
            best, best_score = i, score
    if best is None:
        return [], []
    # A header must be followed by data; if nothing below is typed and the row is identical
    # in shape to the rows after it, it's still accepted (all-text tables) — first full row wins.
    header = [best]
    # extend downward: sub-header rows. A row continues the header when every column whose
    # data (below it) is typed holds text in that row, or when it is joined to the header
    # by merged cells.
    while header[-1] + 1 < len(rows) - 1 and len(header) < 3:
        nxt = header[-1] + 1
        if not _is_headerish(rows[nxt], width):
            break
        sample = rows[nxt + 1 : nxt + 31]
        typed_cols = []
        for c in range(width):
            kinds = [k for k in (cell_kind(r[c]) for r in sample) if k != "empty"]
            if kinds and sum(k in ("num", "date", "bool") for k in kinds) / len(kinds) >= 0.6:
                typed_cols.append(c)
        text_in_typed = typed_cols and all(cell_kind(rows[nxt][c]) in ("text", "empty") for c in typed_cols)
        if text_in_typed or (header[-1] in merged_rows and nxt in merged_rows):
            header.append(nxt)
        else:
            break
    # extend upward: group header rows (merged / multiple distinct labels)
    while header[0] - 1 >= 0 and len(header) < 3:
        up = header[0] - 1
        vals = [normalize_text(v) for v in rows[up] if normalize_text(v)]
        nonempty, text, _ = _row_stats(rows[up])
        if nonempty and text == nonempty and len(set(vals)) >= 2 and (up in merged_rows or nonempty < width):
            header.insert(0, up)
        else:
            break
    titles = [i for i in range(header[0]) if any(normalize_text(v) for v in rows[i])]
    return header, titles


def _combine_labels(rows: list[list[Any]], header: list[int], width: int) -> list[str | None]:
    labels: list[str | None] = []
    for c in range(width):
        parts: list[str] = []
        for h in header:
            v = normalize_text(rows[h][c])
            if isinstance(rows[h][c], (datetime, date)):
                v = rows[h][c].strftime("%Y-%m-%d")
            if v and (not parts or parts[-1] != v):
                parts.append(v)
        labels.append(" / ".join(parts) if parts else None)
    return labels


def _dedupe(labels: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for lb in labels:
        k = lb.lower()
        if k in seen:
            seen[k] += 1
            out.append(f"{lb} ({seen[k]})")
        else:
            seen[k] = 1
            out.append(lb)
    return out


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------


def detect_tables(grid: RawGrid) -> tuple[list[DetectedTable], list[str]]:
    """Detect tables in a grid. Returns (tables, sheet_notes)."""
    grid.rect()
    if grid.n_rows == 0 or grid.n_cols == 0:
        return [], [f"Sheet '{grid.name}' is empty."]
    empty = [[is_null(v) for v in row] for row in grid.cells]
    regions = _regions(grid, empty)
    merged_rows_all: set[int] = set()
    merged_cell_count: dict[tuple[int, int, int, int], int] = {}
    for r1, c1, r2, c2 in grid.merged:
        if c2 > c1 or r2 > r1:
            merged_rows_all.update(range(r1, r2 + 1))
        merged_cell_count[(r1, c1, r2, c2)] = (r2 - r1 + 1) * (c2 - c1 + 1) - 1

    tables: list[DetectedTable] = []
    sheet_notes: list[str] = []
    pending_title: str | None = None
    force_first = bool(grid.meta.get("json_header"))

    for reg in regions:
        width = reg.c1 - reg.c0 + 1
        rows = [grid.cells[r][reg.c0 : reg.c1 + 1] for r in range(reg.r0, reg.r1 + 1)]
        fmts = [grid.formats[r][reg.c0 : reg.c1 + 1] for r in range(reg.r0, reg.r1 + 1)] if grid.formats else None
        src_rows = list(range(reg.r0 + 1 + grid.row_offset, reg.r1 + 2 + grid.row_offset))
        nonblank_rows = [i for i, r in enumerate(rows) if any(not is_null(v) for v in r)]

        # Title-only region (e.g. report title, a "Prepared by" note)
        if len(nonblank_rows) <= 2 and all(_is_sparse_title(rows[i], width) for i in nonblank_rows) and width >= 1:
            texts = [normalize_text(v) for i in nonblank_rows for v in rows[i] if normalize_text(v)]
            texts = list(dict.fromkeys(texts))
            if tables and tables[-1].meta.get("_c0") == reg.c0 and reg.r0 > tables[-1].meta.get("_r1", -1):
                tables[-1].notes.append(f"Note below table (row {src_rows[0]}): {' | '.join(texts)}")
                pending_title = " — ".join(texts) if len(texts) else None
            else:
                pending_title = " — ".join(texts) if texts else None
            continue

        rel_merged = {r - reg.r0 for r in merged_rows_all if reg.r0 <= r <= reg.r1}
        header, titles = detect_header(rows, width, rel_merged, force_first=force_first)

        # Continuation of the previous table (blank rows inside data, no header of its own)
        prev = tables[-1] if tables else None
        same_shape = prev is not None and prev.meta.get("_c0") == reg.c0 and prev.meta.get("_width") == width
        if same_shape and header and reg.r0 - prev.meta["_r1"] <= 4 and _matches_data_profile(rows[header[0]], prev):
            header = []  # the "header" candidate is just another data row
        if same_shape and not header:
            gap = reg.r0 - prev.meta["_r1"] - 1
            prev.excluded.append(ExcludedRow(prev.meta["_r1"] + 2 + grid.row_offset, f"{gap} blank row(s) inside table", {}))
            _append_data(prev, rows, src_rows, fmts, list(range(len(rows))), reg.c0)
            prev.meta["_r1"] = reg.r1
            continue
        if same_shape and header:
            # repeated header (e.g. page break) after a gap → continuation as well
            labels_here = _combine_labels(rows, header, width)
            if labels_here == prev.original_labels and header[0] == 0:
                prev.excluded.append(ExcludedRow(src_rows[header[-1]], "repeated header row", dict(zip(prev.column_letters, (normalize_text(v) for v in rows[header[-1]])))))
                _append_data(prev, rows, src_rows, fmts, list(range(header[-1] + 1, len(rows))), reg.c0)
                prev.meta["_r1"] = reg.r1
                continue

        title_parts = [normalize_text(v) for i in titles for v in rows[i] if normalize_text(v)]
        title_parts = list(dict.fromkeys(title_parts))
        title = " — ".join(title_parts) if title_parts else pending_title
        pending_title = None
        orig_labels = _combine_labels(rows, header, width) if header else [None] * width
        tbl = DetectedTable(
            sheet=grid.name,
            index=len(tables),
            origin=grid.origin,
            labels=[],
            original_labels=orig_labels,
            rows=[],
            source_rows=[],
            source_cols=[reg.c0 + 1 + grid.col_offset + c for c in range(width)],
            formats=[] if fmts is not None else None,
            header_rows=[src_rows[h] for h in header],
            title=title,
            meta={"_c0": reg.c0, "_r1": reg.r1, "_width": width, **{k: v for k, v in grid.meta.items() if k not in ("last_page",)}},
        )
        if not header:
            tbl.notes.append("No header row detected; generic column names were assigned.")
        for i in titles:
            tbl.notes.append(f"Title row {src_rows[i]}: {' '.join(normalize_text(v) for v in rows[i] if normalize_text(v))}")
        start = (header[-1] + 1) if header else 0
        _append_data(tbl, rows, src_rows, fmts, list(range(start, len(rows))), reg.c0)
        tables.append(tbl)

    # finalize: labels, trailing notes, empty columns, merged-cell accounting
    out: list[DetectedTable] = []
    for t in tables:
        _strip_trailing_notes(t)
        _drop_empty_columns(t)
        if not t.rows:
            sheet_notes.append(f"Region '{t.name}' had a header but no data rows; skipped.")
            continue
        width = len(t.source_cols)
        labels = []
        for c in range(width):
            lb = t.original_labels[c]
            labels.append(lb if lb else f"Column {t.column_letters[c]}")
        t.labels = _dedupe(labels)
        t.merged_filled = sum(
            n for (r1, c1, r2, c2), n in merged_cell_count.items()
            if any(r1 + 1 + grid.row_offset <= sr <= r2 + 1 + grid.row_offset for sr in t.source_rows)
            and any(c1 + 1 <= sc <= c2 + 1 for sc in t.source_cols)
        )
        if t.merged_filled:
            t.notes.append(f"{t.merged_filled} merged cell(s) inside the data were filled with their merged value.")
        for k in ("_c0", "_r1", "_width"):
            t.meta.pop(k, None)
        t.index = len(out)
        out.append(t)
    # second pass: rename duplicates of table names within the sheet
    return out, sheet_notes


def _matches_data_profile(row: list[Any], prev: DetectedTable) -> bool:
    """True if the row's cell kinds look like the previous table's data rows."""
    sample = prev.rows[-30:]
    if not sample:
        return False
    agree = total = 0
    for c, v in enumerate(row):
        k = cell_kind(v)
        if k == "empty":
            continue
        kinds = [cell_kind(r[c]) for r in sample if c < len(r)]
        kinds = [x for x in kinds if x != "empty"]
        if not kinds:
            continue
        total += 1
        dominant = max(set(kinds), key=kinds.count)
        agree += k == dominant
    return total > 0 and agree / total >= 0.8


def _append_data(t: DetectedTable, rows, src_rows, fmts, idxs: list[int], c0: int) -> None:
    labels_norm = [normalize_text(x) for x in t.original_labels]
    for i in idxs:
        row = rows[i]
        if all(is_null(v) for v in row):
            continue
        norm = [normalize_text(v) for v in row]
        if any(labels_norm) and norm == labels_norm:
            t.excluded.append(ExcludedRow(src_rows[i], "repeated header row", dict(zip(t.column_letters, norm))))
            continue
        first = next((v for v in norm if v), None)
        if first and TOTAL_RE.match(first) and any(cell_kind(v) == "num" for v in row):
            t.excluded.append(ExcludedRow(src_rows[i], "total row", dict(zip(t.column_letters, (_disp(v) for v in row)))))
            continue
        t.rows.append(list(row))
        t.source_rows.append(src_rows[i])
        if t.formats is not None and fmts is not None:
            t.formats.append(list(fmts[i]))


def _disp(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    return normalize_text(v)


def _strip_trailing_notes(t: DetectedTable) -> None:
    width = len(t.source_cols)
    if width < 3:
        return
    while t.rows:
        row = t.rows[-1]
        vals = [v for v in row if not is_null(v)]
        if len(vals) == 1 and cell_kind(vals[0]) == "text" and len(normalize_text(vals[0]) or "") >= 12:
            t.excluded.append(ExcludedRow(t.source_rows[-1], "note row below table", dict(zip(t.column_letters, (_disp(v) for v in row)))))
            t.rows.pop()
            t.source_rows.pop()
            if t.formats:
                t.formats.pop()
        else:
            break


def _drop_empty_columns(t: DetectedTable) -> None:
    width = len(t.source_cols)
    keep = [
        c for c in range(width)
        if t.original_labels[c] or any(not is_null(r[c]) for r in t.rows)
    ]
    if len(keep) == width:
        return
    dropped = [get_column_letter(t.source_cols[c]) for c in range(width) if c not in keep]
    t.notes.append(f"Removed completely empty column(s): {', '.join(dropped)}.")
    t.rows = [[r[c] for c in keep] for r in t.rows]
    if t.formats:
        t.formats = [[r[c] for c in keep] for r in t.formats]
    t.original_labels = [t.original_labels[c] for c in keep]
    t.source_cols = [t.source_cols[c] for c in keep]
