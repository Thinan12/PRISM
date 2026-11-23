"""Apply the approved parts of a Blueprint to a dataset version → a new IngestedTable.

Only ``approved`` operations run. Order: casts → value mappings → column exclusions →
duplicate removal. The raw (source-text) layer is never modified; every row removed from
the typed layer is recorded in lineage with its reason.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.blueprint.context import DatasetContext
from app.blueprint.schema import DatasetPlan, OpCastColumn, OpExcludeColumn, OpMapValues, OpRemoveDuplicates
from app.ingestion.parsers import FAIL, is_null, parse_date, parse_number
from app.ingestion.pipeline import IngestedTable

ORDER = {"cast_column": 0, "map_values": 1, "exclude_column": 2, "remove_duplicate_rows": 3}


def execute_plan(plan: DatasetPlan, ctx: DatasetContext, blueprint_id: int) -> tuple[IngestedTable, list[dict[str, Any]]]:
    if ctx.raw is None:
        raise ValueError("raw layer required")
    df = ctx.df.copy()
    raw = ctx.raw.copy()
    columns = [dict(c) for c in ctx.columns]
    lineage = dict(ctx.lineage)
    lineage["excluded_rows"] = list(lineage.get("excluded_rows", []))
    # previous failures survive (they still describe unreadable source cells)
    failures: dict[str, list[tuple[int, str]]] = {}
    for p in ctx.profile.get("columns", []):
        ex = p.get("failure_examples_all") or p.get("failure_examples") or []
        if ex:
            failures[p["key"]] = [(e["row"], e["value"]) for e in ex]
    log: list[dict[str, Any]] = []
    ops = sorted((o for o in plan.operations if o.status == "approved"), key=lambda o: ORDER[o.spec.op])

    for op in ops:
        s = op.spec
        if isinstance(s, OpCastColumn):
            meta = next(c for c in columns if c["key"] == s.column)
            src = raw[s.column] if s.column in raw.columns else df[s.column].astype("string")
            vals, fails = [], []
            for i, v in enumerate(src.tolist()):
                if v is None or v is pd.NA or is_null(v):
                    vals.append(None)
                    continue
                r: Any
                if s.to_type in ("integer", "number"):
                    r = parse_number(v)
                    if r is not FAIL and s.to_type == "integer" and float(r) != int(float(r)):
                        r = FAIL
                elif s.to_type == "date":
                    r = parse_date(v, meta.get("date_order") or "dmy")
                else:
                    r = str(v)
                if r is FAIL:
                    fails.append((int(df["_source_row"].iloc[i]), str(v)))
                    vals.append(None)
                else:
                    vals.append(r)
            if s.to_type == "integer":
                df[s.column] = pd.array([None if v is None else int(v) for v in vals], dtype="Int64")
            elif s.to_type == "number":
                df[s.column] = pd.array([None if v is None else float(v) for v in vals], dtype="Float64")
            elif s.to_type == "date":
                df[s.column] = pd.to_datetime(pd.Series(vals, dtype="object"), errors="coerce").astype("datetime64[us]")
            else:
                df[s.column] = pd.array(vals, dtype="string")
            meta["logical_type"] = s.to_type
            meta["notes"] = list(meta.get("notes", [])) + [f"Re-typed as {s.to_type} by blueprint #{blueprint_id}."]
            meta["parse_failures"] = len(fails)
            failures[s.column] = fails
            log.append({"op": op.id, "kind": s.op, "column": s.column, "failures": len(fails)})
        elif isinstance(s, OpMapValues):
            mapping = {v: m.to for m in s.mappings for v in m.variants}
            col = df[s.column]
            mask = col.isin(list(mapping))
            n = int(mask.fillna(False).sum())
            df.loc[mask.fillna(False), s.column] = col[mask.fillna(False)].map(mapping)
            meta = next(c for c in columns if c["key"] == s.column)
            meta["notes"] = list(meta.get("notes", [])) + [f"{n} value(s) unified by blueprint #{blueprint_id}: " + "; ".join(f"{' / '.join(m.variants)} → {m.to}" for m in s.mappings)[:300]]
            log.append({"op": op.id, "kind": s.op, "column": s.column, "rows": n})
        elif isinstance(s, OpExcludeColumn):
            if s.column in df.columns:
                df = df.drop(columns=[s.column])
            columns = [c for c in columns if c["key"] != s.column]
            failures.pop(s.column, None)
            lineage.setdefault("notes", [])
            lineage["notes"] = list(lineage["notes"]) + [f"Column '{s.column}' excluded from the typed data by blueprint #{blueprint_id} (still in the raw layer)."]
            log.append({"op": op.id, "kind": s.op, "column": s.column})
        elif isinstance(s, OpRemoveDuplicates):
            keys = [c["key"] for c in columns]
            dup = df[keys].astype("string").duplicated(keep="first")
            removed = df.loc[dup, "_source_row"].astype(int).tolist()
            df = df.loc[~dup].reset_index(drop=True)
            for r in removed:
                lineage["excluded_rows"].append({"source_row": r, "reason": f"duplicate removed (blueprint #{blueprint_id})", "values": {}})
            log.append({"op": op.id, "kind": s.op, "rows": len(removed), "source_rows": removed[:1000]})

    # approved display names become labels (keys stay stable for SQL)
    names = {c.key: c.display_name for c in plan.columns if c.status == "approved"}
    roles = {c.key: c.semantic_role.value for c in plan.columns if c.status != "rejected"}
    descs = {c.key: c.description for c in plan.columns if c.status != "rejected" and c.description}
    for c in columns:
        if c["key"] in names and names[c["key"]] != c["label"]:
            c["original_label"] = c.get("original_label") or c["label"]
            c["label"] = names[c["key"]]
        if c["key"] in roles:
            c["semantic_role"] = roles[c["key"]]
        if c["key"] in descs:
            c["description"] = descs[c["key"]]
    # failures must point at rows still present
    present = set(df["_source_row"].astype(int).tolist())
    failures = {k: [f for f in v if f[0] in present] for k, v in failures.items() if any(f[0] in present for f in v)}
    lineage["blueprint"] = {"blueprint_id": blueprint_id, "operations": log, "from_version": ctx.version}
    df["_prism_row"] = pd.array(range(1, len(df) + 1), dtype="Int64")
    # keep the raw layer row-aligned with the typed layer (removed rows are in lineage and in
    # earlier versions; the original file is never modified)
    raw = raw[raw["_source_row"].astype(int).isin(present)].reset_index(drop=True)
    raw["_prism_row"] = pd.array(range(1, len(raw) + 1), dtype="Int64")
    t = IngestedTable(name=plan.title or ctx.name, sheet=lineage.get("sheet", ""), origin=lineage.get("origin", ""),
                      title=lineage.get("title"), columns=columns, clean=df, raw=raw, lineage=lineage, failures=failures)
    return t, log
