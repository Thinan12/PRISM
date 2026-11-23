"""Deterministic dataset profiling and data-quality assessment.

All figures are computed from the typed data; nothing here is estimated by AI.
Quality score (0-100) = 100 − weighted penalties, each capped, documented in ``SCORING``.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.ingestion.parsers import parse_number
from app.ingestion.pipeline import IngestedTable

TZ = ZoneInfo("Asia/Vientiane")
SCORING = {
    "missing": "up to −30: average share of missing values across columns",
    "parse_failures": "up to −25: share of values that could not be converted to the column type",
    "duplicates": "up to −20: share of rows that are exact duplicates",
    "inconsistent_spelling": "up to −10: category columns with spelling variants",
    "total_mismatch": "−10 per source total row that disagrees with the computed sum (max −15)",
}


def _f(x: Any) -> Any:
    """JSON-safe scalar."""
    if x is None or x is pd.NA or x is pd.NaT:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if hasattr(x, "item"):
        try:
            x = x.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, float):
        return round(x, 6)
    return x


def fmt_duration(sec: float | None) -> str | None:
    if sec is None:
        return None
    sec = int(round(sec))
    h, rem = divmod(abs(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{'-' if sec < 0 else ''}{h}:{m:02d}:{s:02d}"


def _variant_key(s: str) -> str:
    return re.sub(r"[\s_\-.]+", " ", s.casefold()).strip()


def profile_column(s: pd.Series, meta: dict[str, Any], raw: pd.Series, failures: list[tuple[int, str]], n_rows: int) -> dict[str, Any]:
    t = meta["logical_type"]
    non_null = int(s.notna().sum())
    raw_present = int(raw.notna().sum())
    missing = n_rows - raw_present
    p: dict[str, Any] = {
        "key": meta["key"],
        "label": meta["label"],
        "logical_type": t,
        "unit": meta.get("unit"),
        "semantic_hint": meta.get("semantic_hint"),
        "non_null": non_null,
        "missing": missing,
        "missing_pct": round(missing / n_rows * 100, 2) if n_rows else 0.0,
        "parse_failures": len(failures),
        "failure_examples": [{"row": r, "value": v} for r, v in failures[:10]],
        "distinct": int(s.nunique(dropna=True)),
    }
    p["unique_ratio"] = round(p["distinct"] / non_null, 4) if non_null else 0.0
    vals = s.dropna()
    if t in ("integer", "number", "duration") and non_null:
        f = vals.astype("float64")
        q1, q3 = f.quantile(0.25), f.quantile(0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = f[(f < lo) | (f > hi)] if iqr > 0 else f.iloc[0:0]
        p.update({
            "min": _f(f.min()), "max": _f(f.max()), "sum": _f(f.sum()), "mean": _f(f.mean()),
            "median": _f(f.median()), "std": _f(f.std()) if len(f) > 1 else None,
            "p25": _f(q1), "p75": _f(q3), "zeros": int((f == 0).sum()), "negatives": int((f < 0).sum()),
            "outliers": int(len(outliers)), "outlier_bounds": [_f(lo), _f(hi)] if iqr > 0 else None,
        })
        if t == "duration":
            p["display"] = {k: fmt_duration(p[k]) for k in ("min", "max", "mean", "median", "sum")}
    elif t in ("date", "datetime") and non_null:
        now = datetime.now(TZ).replace(tzinfo=None)
        p.update({
            "min": _f(vals.min()), "max": _f(vals.max()),
            "span_days": int((vals.max() - vals.min()).days),
            "future": int((vals > pd.Timestamp(now)).sum()),
            "by_month": {str(k): int(v) for k, v in vals.dt.to_period("M").value_counts().sort_index().items()},
        })
        if len(p["by_month"]) > 60:
            p["by_month"] = dict(list(p["by_month"].items())[-60:])
    elif t == "boolean" and non_null:
        p.update({"true": int(vals.sum()), "false": int((~vals.astype(bool)).sum())})
    if t in ("text", "phone", "boolean", "integer") or meta.get("semantic_hint") == "category":
        vc = vals.astype("string").value_counts().head(15)
        p["top_values"] = [{"value": str(k), "count": int(v)} for k, v in vc.items()]
    if t in ("text", "phone") and non_null:
        lens = vals.astype("string").str.len()
        p.update({"min_length": int(lens.min()), "max_length": int(lens.max())})
        if t == "text":
            groups: dict[str, set[str]] = defaultdict(set)
            for v in vals.astype("string").unique()[:50_000]:
                groups[_variant_key(str(v))].add(str(v))
            variants = [sorted(g) for g in groups.values() if len(g) > 1]
            p["spelling_variants"] = variants[:20]
            p["spelling_variant_groups"] = len(variants)
    p["samples"] = [_f(v) if not isinstance(v, str) else v for v in vals.drop_duplicates().head(5).tolist()]
    if t == "duration":
        p["samples_display"] = [fmt_duration(v) for v in p["samples"]]
    p["has_lao"] = meta.get("has_lao", False)
    return p


def _bounds(c: dict[str, Any]) -> str:
    lo, hi = c["outlier_bounds"]
    if c["logical_type"] == "duration":
        return f"{fmt_duration(max(lo, 0))}–{fmt_duration(hi)}"
    unit = " ₭" if c.get("unit") == "LAK" else ""
    return f"{max(lo, 0) if c.get('negatives', 0) == 0 else lo:,.0f}–{hi:,.0f}{unit}"


def _total_checks(table: IngestedTable) -> list[dict[str, Any]]:
    """Verify each source 'Total' row against the computed sum of the rows above it."""
    checks = []
    cols = table.columns
    df = table.clean
    excluded = [e for e in table.lineage["excluded_rows"] if e["reason"] == "total row"]
    prev_total = 0
    for e in excluded:
        above_all = df[df["_source_row"] < e["source_row"]]
        segment = above_all[above_all["_source_row"] > prev_total]
        prev_total = e["source_row"]
        for c in cols:
            if c["logical_type"] not in ("integer", "number"):
                continue
            raw_v = e["values"].get(c["source_column"])
            if raw_v is None:
                continue
            stated = parse_number(raw_v)
            if not isinstance(stated, (int, float)):
                continue
            seg_sum = float(segment[c["key"]].astype("Float64").sum())
            all_sum = float(above_all[c["key"]].astype("Float64").sum())
            # subtotal (rows since previous total) or grand total (all rows above)
            computed = seg_sum if abs(float(stated) - seg_sum) <= abs(float(stated) - all_sum) else all_sum
            diff = float(stated) - computed
            checks.append({
                "source_row": e["source_row"], "column": c["key"], "label": c["label"],
                "stated": _f(stated), "computed": _f(computed), "difference": _f(diff),
                "matches": abs(diff) <= max(0.005, abs(computed) * 1e-9),
            })
    return checks


def profile_table(table: IngestedTable) -> dict[str, Any]:
    df, raw = table.clean, table.raw
    n = len(df)
    data_cols = table.data_columns
    cols = [
        profile_column(df[c["key"]], c, raw[c["key"]], table.failures.get(c["key"], []), n)
        for c in table.columns
    ]
    dup_mask = df[data_cols].astype("string").duplicated(keep=False) if n else pd.Series([], dtype=bool)
    dup_rows = int(df[data_cols].astype("string").duplicated(keep="first").sum()) if n else 0
    dup_groups = []
    if dup_rows:
        d = df.loc[dup_mask, ["_source_row", *data_cols]].copy()
        d["_k"] = d[data_cols].astype("string").fillna("∅").agg("␟".join, axis=1)
        for _, g in list(d.groupby("_k", sort=False))[:25]:
            dup_groups.append([int(x) for x in g["_source_row"].tolist()])
    total_checks = _total_checks(table)

    issues: list[dict[str, Any]] = []
    for c in cols:
        if c["missing"]:
            sev = "warning" if c["missing_pct"] >= 20 else "info"
            issues.append({"severity": sev, "code": "missing_values", "column": c["key"], "count": c["missing"],
                           "message": f"{c['label']}: {c['missing']} missing value(s) ({c['missing_pct']}%)."})
        if c["parse_failures"]:
            issues.append({"severity": "warning", "code": "parse_failures", "column": c["key"], "count": c["parse_failures"],
                           "message": f"{c['label']}: {c['parse_failures']} value(s) do not match type {c['logical_type']}; kept in raw data, blank in typed data.",
                           "examples": c["failure_examples"]})
        if c.get("spelling_variant_groups"):
            ex = "; ".join(" / ".join(g) for g in c["spelling_variants"][:3])
            issues.append({"severity": "warning", "code": "inconsistent_spelling", "column": c["key"], "count": c["spelling_variant_groups"],
                           "message": f"{c['label']}: {c['spelling_variant_groups']} group(s) of spelling variants (e.g. {ex})."})
        if c.get("negatives") and c["logical_type"] == "duration":
            issues.append({"severity": "warning", "code": "negative_duration", "column": c["key"], "count": c["negatives"],
                           "message": f"{c['label']}: {c['negatives']} negative duration(s)."})
        if c.get("outliers"):
            issues.append({"severity": "info", "code": "outliers", "column": c["key"], "count": c["outliers"],
                           "message": f"{c['label']}: {c['outliers']} statistical outlier(s) outside the typical range {_bounds(c)}."})
        if c.get("future"):
            issues.append({"severity": "warning", "code": "future_dates", "column": c["key"], "count": c["future"],
                           "message": f"{c['label']}: {c['future']} date(s) are in the future."})
        if c["logical_type"] == "empty":
            issues.append({"severity": "info", "code": "empty_column", "column": c["key"], "count": n,
                           "message": f"{c['label']}: column has a header but no values."})
    if dup_rows:
        issues.append({"severity": "warning", "code": "duplicate_rows", "column": None, "count": dup_rows,
                       "message": f"{dup_rows} row(s) are exact duplicates of an earlier row (kept; flagged for review).",
                       "groups": dup_groups})
    for e in table.lineage["excluded_rows"]:
        if e["reason"] in ("total row", "note row below table", "repeated header row"):
            issues.append({"severity": "info", "code": "excluded_row", "column": None, "count": 1,
                           "message": f"Source row {e['source_row']} excluded from data: {e['reason']}."})
    mismatches = [tc for tc in total_checks if not tc["matches"]]
    for tc in mismatches:
        issues.append({"severity": "error", "code": "total_mismatch", "column": tc["column"], "count": 1,
                       "message": f"Source total in row {tc['source_row']} for {tc['label']} is {tc['stated']}, but the rows above sum to {tc['computed']} (difference {tc['difference']})."})

    data_profiles = [c for c in cols if c["logical_type"] != "empty"]
    avg_missing = sum(c["missing_pct"] for c in data_profiles) / len(data_profiles) / 100 if data_profiles else 0
    total_cells = sum(c["non_null"] + c["parse_failures"] for c in data_profiles) or 1
    fail_share = sum(c["parse_failures"] for c in data_profiles) / total_cells
    dup_share = dup_rows / n if n else 0
    variant_cols = sum(1 for c in data_profiles if c.get("spelling_variant_groups"))
    penalties = {
        "missing": round(min(30, avg_missing * 100), 2),
        "parse_failures": round(min(25, fail_share * 250), 2),
        "duplicates": round(min(20, dup_share * 200), 2),
        "inconsistent_spelling": round(min(10, variant_cols * 2.5), 2),
        "total_mismatch": min(15, 10 * len(mismatches)),
    }
    score = max(0.0, round(100 - sum(penalties.values()), 1))
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (sev_rank[i["severity"]], -(i.get("count") or 0)))
    return {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "row_count": n,
        "column_count": len(cols),
        "duplicate_rows": dup_rows,
        "duplicate_groups": dup_groups,
        "completeness_pct": round(100 - avg_missing * 100, 2),
        "quality_score": score,
        "quality_penalties": penalties,
        "scoring_method": SCORING,
        "total_checks": total_checks,
        "columns": cols,
        "issues": issues,
    }
