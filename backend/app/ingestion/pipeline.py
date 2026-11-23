"""Turn source bytes into typed, lineage-carrying tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.ingestion.coltypes import ColumnResult, infer_column, make_key
from app.ingestion.detect import DetectedTable, detect_tables
from app.ingestion.readers import ImageOCR, read_source

LINEAGE_COLS = ("_prism_row", "_source_sheet", "_source_row")


@dataclass
class IngestedTable:
    name: str
    sheet: str
    origin: str
    title: str | None
    columns: list[dict[str, Any]]
    clean: pd.DataFrame
    raw: pd.DataFrame
    lineage: dict[str, Any]
    failures: dict[str, list[tuple[int, str]]] = field(default_factory=dict)  # key -> (source_row, raw)

    @property
    def data_columns(self) -> list[str]:
        return [c["key"] for c in self.columns]


@dataclass
class IngestResult:
    kind: str
    sheets: list[dict[str, Any]]
    tables: list[IngestedTable]
    notes: list[str]


def _series(col: ColumnResult) -> pd.Series:
    t = col.logical_type
    if t in ("integer", "duration"):
        return pd.Series(pd.array(col.values, dtype="Int64"))
    if t == "number":
        return pd.Series(pd.array([None if v is None else float(v) for v in col.values], dtype="Float64"))
    if t == "boolean":
        return pd.Series(pd.array(col.values, dtype="boolean"))
    if t in ("date", "datetime"):
        return pd.Series(pd.to_datetime(pd.Series(col.values, dtype="object"), errors="coerce")).astype("datetime64[us]")
    return pd.Series(pd.array([None if v is None else str(v) for v in col.values], dtype="string"))


def build_table(t: DetectedTable) -> IngestedTable:
    used: set[str] = set()
    results: list[ColumnResult] = []
    for c, label in enumerate(t.labels):
        cells = [r[c] if c < len(r) else None for r in t.rows]
        fmts = [r[c] if c < len(r) else None for r in t.formats] if t.formats else None
        key = make_key(label, c, used)
        results.append(infer_column(label, t.original_labels[c], t.column_letters[c], cells, fmts, key))

    clean = pd.DataFrame({r.key: _series(r) for r in results})
    raw = pd.DataFrame({r.key: pd.array(r.raw, dtype="string") for r in results})
    n = len(t.rows)
    for df in (clean, raw):
        df.insert(0, "_prism_row", pd.array(range(1, n + 1), dtype="Int64"))
        df["_source_sheet"] = pd.array([t.sheet] * n, dtype="string")
        df["_source_row"] = pd.array(t.source_rows, dtype="Int64")

    failures = {r.key: [(t.source_rows[i], v) for i, v in r.failures] for r in results if r.failures}
    lineage = {
        "sheet": t.sheet,
        "origin": t.origin,
        "title": t.title,
        "header_rows": t.header_rows,
        "source_columns": t.column_letters,
        "first_row": t.source_rows[0] if t.source_rows else None,
        "last_row": t.source_rows[-1] if t.source_rows else None,
        "excluded_rows": [
            {"source_row": e.source_row, "reason": e.reason, "values": e.values} for e in t.excluded
        ],
        "notes": t.notes,
        "merged_cells_filled": t.merged_filled,
        "meta": {k: v for k, v in t.meta.items() if isinstance(v, (str, int, float, bool, list))},
    }
    return IngestedTable(
        name=t.name, sheet=t.sheet, origin=t.origin, title=t.title,
        columns=[r.meta() for r in results], clean=clean, raw=raw, lineage=lineage, failures=failures,
    )


def ingest(data: bytes, filename: str | None, kind: str | None = None, ocr: ImageOCR | None = None) -> IngestResult:
    kind, grids = read_source(data, filename, kind=kind, ocr=ocr)
    tables: list[IngestedTable] = []
    notes: list[str] = []
    sheets = []
    for g in grids:
        detected, sheet_notes = detect_tables(g)
        notes.extend(sheet_notes)
        sheets.append({
            "name": g.name, "rows": g.n_rows, "cols": g.n_cols, "merged_ranges": len(g.merged),
            "tables": len(detected), "hidden": bool(g.meta.get("hidden")),
        })
        for d in detected:
            tables.append(build_table(d))
    if len(tables) > 1:
        seen: dict[str, int] = {}
        for t in tables:
            seen[t.name] = seen.get(t.name, 0) + 1
            if seen[t.name] > 1:
                t.name = f"{t.name} #{seen[t.name]}"
    return IngestResult(kind=kind, sheets=sheets, tables=tables, notes=notes)
