"""KPI → SQL compilation and deterministic evaluation with DuckDB.

KPIs are declarative (aggregation + column + optional filter). They are validated against
the dataset schema, compiled to a read-only DuckDB query over the dataset, and executed
with bound parameters. The rendered SQL (with literals inlined) is stored for provenance.
"""

from __future__ import annotations

import math
from typing import Any

import duckdb

from app.blueprint.context import DatasetContext
from app.blueprint.schema import Aggregation, Kpi, KpiFormat

NUMERIC = {"integer", "number", "duration"}
FILTERABLE = {"text", "phone", "boolean"}


class KpiInvalid(ValueError):
    pass


def ident(key: str) -> str:
    return '"' + key.replace('"', '""') + '"'


def lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def validate_kpi(k: Kpi, ctx: DatasetContext) -> None:
    """Raise KpiInvalid if the KPI cannot be computed on this dataset."""
    a = k.aggregation
    if a in (Aggregation.count_rows, Aggregation.share_of_rows):
        if k.column is not None and a == Aggregation.count_rows:
            raise KpiInvalid("count_rows takes no column")
    else:
        if not k.column:
            raise KpiInvalid(f"{a.value} requires a column")
        col = ctx.col(k.column)
        if col is None:
            raise KpiInvalid(f"unknown column '{k.column}'")
        if a in (Aggregation.sum, Aggregation.average, Aggregation.minimum, Aggregation.maximum) and col["logical_type"] not in NUMERIC:
            raise KpiInvalid(f"{a.value} needs a numeric or duration column; '{col['label']}' is {col['logical_type']}")
    if a == Aggregation.share_of_rows and k.filter is None:
        raise KpiInvalid("share_of_rows requires a filter")
    if k.filter is not None:
        fc = ctx.col(k.filter.column)
        if fc is None:
            raise KpiInvalid(f"unknown filter column '{k.filter.column}'")
        if fc["logical_type"] not in FILTERABLE:
            raise KpiInvalid(f"filter column '{fc['label']}' must be text, phone or yes/no")
        present = ctx.distinct_values(k.filter.column) if fc["logical_type"] != "boolean" else {"True", "False"}
        missing = [v for v in k.filter.values if v not in present]
        if missing:
            raise KpiInvalid(f"filter value(s) not present in '{fc['label']}': {missing[:5]}")


def natural_format(k: Kpi, ctx: DatasetContext) -> KpiFormat:
    if k.aggregation == Aggregation.share_of_rows:
        return KpiFormat.percent
    if k.aggregation in (Aggregation.count_rows, Aggregation.count_distinct) or not k.column:
        return KpiFormat.number
    col = ctx.col(k.column) or {}
    if col.get("logical_type") == "duration":
        return KpiFormat.duration
    if col.get("unit") == "LAK":
        return KpiFormat.lak
    if col.get("unit") == "percent":
        return KpiFormat.percent
    return KpiFormat.number


def compile_kpi(k: Kpi, ctx: DatasetContext, table: str = "data") -> tuple[str, list[Any], str]:
    """Return (parameterized_sql, params, display_sql)."""
    where, params, where_disp = "", [], ""
    if k.filter is not None:
        ph = ", ".join("?" for _ in k.filter.values)
        cond = f"CAST({ident(k.filter.column)} AS VARCHAR) IN ({ph})"
        cond_disp = f"CAST({ident(k.filter.column)} AS VARCHAR) IN ({', '.join(lit(v) for v in k.filter.values)})"
        params = list(k.filter.values)
    a = k.aggregation
    c = ident(k.column) if k.column else None
    fn = {Aggregation.sum: "SUM", Aggregation.average: "AVG", Aggregation.minimum: "MIN", Aggregation.maximum: "MAX"}
    if a == Aggregation.share_of_rows:
        expr = f"COUNT(*) FILTER (WHERE {cond}) * 1.0 / NULLIF(COUNT(*), 0)"
        expr_disp = f"COUNT(*) FILTER (WHERE {cond_disp}) * 1.0 / NULLIF(COUNT(*), 0)"
    else:
        if k.filter is not None:
            where, where_disp = f" WHERE {cond}", f" WHERE {cond_disp}"
        if a == Aggregation.count_rows:
            expr = expr_disp = "COUNT(*)"
        elif a == Aggregation.count_distinct:
            expr = expr_disp = f"COUNT(DISTINCT {c})"
        else:
            expr = expr_disp = f"{fn[a]}({c})"
    sql = f"SELECT {expr} AS value FROM {table}{where}"
    disp = f"SELECT {expr_disp} AS value FROM {table}{where_disp}"
    return sql, params, disp


def evaluate_kpis(kpis: list[Kpi], ctx: DatasetContext) -> list[str]:
    """Validate, compile and compute every KPI in place. Returns warnings for invalid KPIs
    (which are marked rejected rather than silently dropped)."""
    warnings: list[str] = []
    con = duckdb.connect(config={"enable_external_access": False})
    try:
        con.register("data", ctx.df.drop(columns=[c for c in ("_source_sheet",) if c in ctx.df.columns]))
        for k in kpis:
            try:
                validate_kpi(k, ctx)
            except KpiInvalid as e:
                k.status = "rejected"
                k.value = None
                k.sql = None
                warnings.append(f"KPI '{k.name}' rejected: {e}")
                continue
            k.format = natural_format(k, ctx)
            sql, params, disp = compile_kpi(k, ctx)
            row = con.execute(sql, params).fetchone()
            v = row[0] if row else None
            k.value = float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
            k.sql = disp
            k.computed_on_version = ctx.version
    finally:
        con.close()
    return warnings
