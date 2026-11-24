"""QuerySpec → validated, parameterized DuckDB SQL → result with provenance.

Safety: identifiers come only from validated column keys (quoted); every value is a bound
parameter; DuckDB runs in-process with external access disabled against an in-memory view of
one immutable dataset version. The display SQL (literals inlined) is exactly the executed
statement and is returned with every result.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
import time
from collections import OrderedDict
from decimal import Decimal
from typing import Any

import duckdb
import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.spec import Analysis, Condition, Dimension, Measure, Op, QuerySpec, TimeGrain
from app.blueprint.context import DatasetContext
from app.blueprint.kpi import ident
from app.blueprint.rules import leaf_label
from app.blueprint.schema import Aggregation
from app.db.models import Dataset, DatasetVersion
from app.services.storage import get_storage

NUMERIC = {"integer", "number", "duration"}
TEMPORAL = {"date", "datetime"}
ORDERED = NUMERIC | TEMPORAL
CATEGORICAL = {"text", "phone", "boolean"}
NO_VALUE_OPS = {Op.is_null, Op.not_null}
SINGLE_VALUE_OPS = {Op.eq, Op.neq, Op.gt, Op.gte, Op.lt, Op.lte, Op.contains}
RANGE_OPS = {Op.gt, Op.gte, Op.lt, Op.lte, Op.between}
GRAIN_INTERVAL = {TimeGrain.day: "INTERVAL 1 DAY", TimeGrain.week: "INTERVAL 7 DAY", TimeGrain.month: "INTERVAL 1 MONTH",
                  TimeGrain.quarter: "INTERVAL 3 MONTH", TimeGrain.year: "INTERVAL 1 YEAR"}
ANOMALY_Z = 3.5
AGG_FN = {Aggregation.sum: "SUM", Aggregation.average: "AVG", Aggregation.minimum: "MIN", Aggregation.maximum: "MAX"}
AGG_WORD = {Aggregation.count_rows: "Records", Aggregation.count_distinct: "Distinct", Aggregation.sum: "Total",
            Aggregation.average: "Average", Aggregation.minimum: "Minimum", Aggregation.maximum: "Maximum",
            Aggregation.share_of_rows: "Share"}
OP_WORD = {Op.eq: "is", Op.neq: "is not", Op.in_: "is one of", Op.not_in: "is not one of", Op.gt: ">", Op.gte: "≥",
           Op.lt: "<", Op.lte: "≤", Op.between: "between", Op.contains: "contains", Op.is_null: "is blank",
           Op.not_null: "is not blank"}


class QueryInvalid(ValueError):
    pass


# ---------------------------------------------------------------------------
# dataset loading (immutable versions → safe to cache)
# ---------------------------------------------------------------------------

_CACHE: OrderedDict[int, DatasetContext] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 12


def dataset_context(db: Session, ds: Dataset, version: int | None = None) -> DatasetContext:
    v = db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id,
                                                DatasetVersion.version == (version or ds.current_version))).scalar_one_or_none()
    if v is None:
        raise QueryInvalid(f"Dataset {ds.id} has no version {version}.")
    with _CACHE_LOCK:
        hit = _CACHE.get(v.id)
        if hit is not None:
            _CACHE.move_to_end(v.id)
            return hit
    ctx = DatasetContext(dataset_id=ds.id, version=v.version, version_id=v.id, name=ds.name, columns=v.columns,
                         profile=v.profile, lineage=v.lineage, df=get_storage().read_parquet(v.clean_path))
    with _CACHE_LOCK:
        _CACHE[v.id] = ctx
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return ctx


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _col(ctx: DatasetContext, key: str, what: str) -> dict[str, Any]:
    c = ctx.col(key)
    if c is None:
        raise QueryInvalid(f"unknown {what} column '{key}'")
    return c


def _coerce(value: Any, col: dict[str, Any]) -> Any:
    t = col["logical_type"]
    label = col["label"]
    if t in NUMERIC:
        if isinstance(value, bool):
            raise QueryInvalid(f"'{label}' needs a number")
        try:
            f = float(value)
        except (TypeError, ValueError) as e:
            raise QueryInvalid(f"'{label}' needs a number, got '{value}'") from e
        if math.isnan(f) or math.isinf(f):
            raise QueryInvalid(f"'{label}' needs a finite number")
        return f
    if t in TEMPORAL:
        try:
            ts = pd.Timestamp(str(value))
        except (ValueError, TypeError) as e:
            raise QueryInvalid(f"'{label}' needs a date (YYYY-MM-DD), got '{value}'") from e
        if ts is pd.NaT or pd.isna(ts):
            raise QueryInvalid(f"'{label}' needs a date (YYYY-MM-DD)")
        return ts.tz_localize(None).to_pydatetime() if ts.tzinfo else ts.to_pydatetime()
    if t == "boolean":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0"):
            return False
        raise QueryInvalid(f"'{label}' is yes/no, got '{value}'")
    return str(value)


def validate_condition(c: Condition, ctx: DatasetContext) -> Condition:
    col = _col(ctx, c.column, "filter")
    t = col["logical_type"]
    if c.op in NO_VALUE_OPS:
        return c.model_copy(update={"values": []})
    if not c.values:
        raise QueryInvalid(f"filter on '{col['label']}' needs a value")
    if c.op in SINGLE_VALUE_OPS and len(c.values) != 1:
        raise QueryInvalid(f"'{OP_WORD[c.op]}' on '{col['label']}' takes exactly one value")
    if c.op == Op.between and len(c.values) != 2:
        raise QueryInvalid(f"'between' on '{col['label']}' takes two values")
    if c.op in RANGE_OPS and t not in ORDERED:
        raise QueryInvalid(f"'{OP_WORD[c.op]}' needs a number, duration or date column; '{col['label']}' is {t}")
    if c.op == Op.contains and t not in ("text", "phone"):
        raise QueryInvalid(f"'contains' needs a text column; '{col['label']}' is {t}")
    values = [_coerce(v, col) for v in c.values] if c.op != Op.contains else [str(c.values[0])]
    if t in ("text", "phone") and c.op in (Op.eq, Op.neq, Op.in_, Op.not_in):
        present = ctx.distinct_values(c.column)
        missing = [v for v in values if v not in present]
        if missing:
            raise QueryInvalid(f"value(s) not found in '{col['label']}': {', '.join(map(str, missing[:5]))}")
    return c.model_copy(update={"values": values})


def validate_measure(m: Measure, ctx: DatasetContext) -> Measure:
    a = m.aggregation
    if a in (Aggregation.count_rows, Aggregation.share_of_rows):
        if m.column is not None:
            m = m.model_copy(update={"column": None})
    else:
        if not m.column:
            raise QueryInvalid(f"{a.value} needs a column")
        col = _col(ctx, m.column, "measure")
        if a in AGG_FN and a not in (Aggregation.minimum, Aggregation.maximum) and col["logical_type"] not in NUMERIC:
            raise QueryInvalid(f"{a.value} needs a number or duration column; '{col['label']}' is {col['logical_type']}")
        if a in (Aggregation.sum, Aggregation.average) and col.get("semantic_hint") == "identifier":
            raise QueryInvalid(f"'{col['label']}' is an identifier (row number or code); a {a.value} of it is meaningless — "
                               "count records instead")
        if a in (Aggregation.minimum, Aggregation.maximum) and col["logical_type"] not in ORDERED:
            raise QueryInvalid(f"{a.value} needs a number, duration or date column; '{col['label']}' is {col['logical_type']}")
    if a == Aggregation.share_of_rows and m.filter is None:
        raise QueryInvalid("share_of_rows needs a filter (the rows to count)")
    flt = validate_condition(m.filter, ctx) if m.filter is not None else None
    return m.model_copy(update={"filter": flt})


def _is_time(d: Dimension, ctx: DatasetContext) -> bool:
    c = ctx.col(d.column)
    return bool(c and c["logical_type"] in TEMPORAL)


def validate(spec: QuerySpec, ctx: DatasetContext) -> QuerySpec:
    """Return a normalised copy of ``spec`` (typed values) or raise QueryInvalid."""
    if spec.dataset_id != ctx.dataset_id:
        raise QueryInvalid("query is for a different dataset")
    measures = [validate_measure(m, ctx) for m in spec.measures]
    dims = []
    for d in spec.dimensions:
        col = _col(ctx, d.column, "group-by")
        if d.grain is not None and col["logical_type"] not in TEMPORAL:
            raise QueryInvalid(f"a time grain needs a date column; '{col['label']}' is {col['logical_type']}")
        dims.append(d)
    if len({(d.column, d.grain) for d in dims}) != len(dims):
        raise QueryInvalid("the same grouping is used twice")
    filters = [validate_condition(c, ctx) for c in spec.filters]
    if spec.analysis in (Analysis.change, Analysis.anomaly):
        if not dims or not _is_time(dims[0], ctx):
            raise QueryInvalid(f"'{spec.analysis.value}' needs a date column as the first grouping")
        if spec.analysis == Analysis.anomaly and len(dims) != 1:
            raise QueryInvalid("anomaly detection works on one time series (one grouping)")
    if spec.sort is not None:
        n = len(measures) if spec.sort.by == "measure" else len(dims)
        if spec.sort.index >= n:
            raise QueryInvalid("sort refers to a measure or grouping that does not exist")
    return spec.model_copy(update={"measures": measures, "dimensions": dims, "filters": filters})


# ---------------------------------------------------------------------------
# compilation
# ---------------------------------------------------------------------------


def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(int(v)) if float(v).is_integer() else repr(v)
    if isinstance(v, dt.datetime):
        return f"TIMESTAMP '{v.isoformat(sep=' ')}'"
    return "'" + str(v).replace("'", "''") + "'"


class _Sql:
    """Builds the parameterized statement and its literal display form side by side."""

    def __init__(self) -> None:
        self.params: list[Any] = []

    def p(self, v: Any) -> tuple[str, str]:
        self.params.append(v)
        return "?", _lit(v)


def _cond(c: Condition, ctx: DatasetContext, b: _Sql) -> tuple[str, str]:
    col = ctx.col(c.column) or {}
    t = col.get("logical_type")
    x = ident(c.column)
    if c.op == Op.is_null:
        return f"{x} IS NULL", f"{x} IS NULL"
    if c.op == Op.not_null:
        return f"{x} IS NOT NULL", f"{x} IS NOT NULL"
    target = f"CAST({x} AS VARCHAR)" if t in ("text", "phone") else x
    vals = list(c.values)
    if c.op == Op.contains:
        esc = str(vals[0]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q, d = b.p(f"%{esc}%")
        return f"{target} ILIKE {q} ESCAPE '\\'", f"{target} ILIKE {d} ESCAPE '\\'"
    if c.op in (Op.in_, Op.not_in):
        ps = [b.p(v) for v in vals]
        qs, ds = ", ".join(p[0] for p in ps), ", ".join(p[1] for p in ps)
        if c.op == Op.in_:
            return f"{target} IN ({qs})", f"{target} IN ({ds})"
        return f"({x} IS NULL OR {target} NOT IN ({qs}))", f"({x} IS NULL OR {target} NOT IN ({ds}))"
    if c.op == Op.neq:
        q, d = b.p(vals[0])
        return f"{target} IS DISTINCT FROM {q}", f"{target} IS DISTINCT FROM {d}"
    if c.op == Op.between:
        lo, hi = vals
        if t in TEMPORAL and isinstance(hi, dt.datetime) and hi.time() == dt.time(0):
            hi = hi + dt.timedelta(days=1)  # a date-only upper bound includes that whole day
            (ql, dl), (qh, dh) = b.p(lo), b.p(hi)
            return f"({x} >= {ql} AND {x} < {qh})", f"({x} >= {dl} AND {x} < {dh})"
        (ql, dl), (qh, dh) = b.p(lo), b.p(hi)
        return f"({x} BETWEEN {ql} AND {qh})", f"({x} BETWEEN {dl} AND {dh})"
    sym = {Op.eq: "=", Op.gt: ">", Op.gte: ">=", Op.lt: "<", Op.lte: "<="}[c.op]
    q, d = b.p(vals[0])
    return f"{target} {sym} {q}", f"{target} {sym} {d}"


def _measure(m: Measure, ctx: DatasetContext, b: _Sql) -> tuple[str, str]:
    a = m.aggregation
    fq = fd = ""
    if m.filter is not None:
        cq, cd = _cond(m.filter, ctx, b)
        fq, fd = f" FILTER (WHERE {cq})", f" FILTER (WHERE {cd})"
    if a == Aggregation.share_of_rows:
        return f"COUNT(*){fq} * 1.0 / NULLIF(COUNT(*), 0)", f"COUNT(*){fd} * 1.0 / NULLIF(COUNT(*), 0)"
    if a == Aggregation.count_rows:
        return f"COUNT(*){fq}", f"COUNT(*){fd}"
    c = ident(m.column or "")
    if a == Aggregation.count_distinct:
        return f"COUNT(DISTINCT {c}){fq}", f"COUNT(DISTINCT {c}){fd}"
    return f"{AGG_FN[a]}({c}){fq}", f"{AGG_FN[a]}({c}){fd}"


def _dim(d: Dimension) -> str:
    x = ident(d.column)
    return f"date_trunc('{d.grain.value}', {x})" if d.grain else x


def compile_query(spec: QuerySpec, ctx: DatasetContext, table: str = "data") -> tuple[str, list[Any], str]:
    """Return (parameterized_sql, params, display_sql) for a *validated* spec.

    Every fragment carries its own parameters; fragments are concatenated in the order they
    appear in the statement text, so positional ``?`` parameters always line up."""
    dims = spec.dimensions
    mb = _Sql()
    sel_q = [f"{_dim(d)} AS d{i}" for i, d in enumerate(dims)]
    sel_d = list(sel_q)
    for i, m in enumerate(spec.measures):
        q, d = _measure(m, ctx, mb)
        sel_q.append(f"{q} AS m{i}")
        sel_d.append(f"{d} AS m{i}")
    wb = _Sql()
    conds = [_cond(c, ctx, wb) for c in spec.filters]
    wq = f" WHERE {' AND '.join(c[0] for c in conds)}" if conds else ""
    wd = f" WHERE {' AND '.join(c[1] for c in conds)}" if conds else ""
    group = f" GROUP BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else ""
    inner_q = f"SELECT {', '.join(sel_q)} FROM {table}{wq}{group}"
    inner_d = f"SELECT {', '.join(sel_d)} FROM {table}{wd}{group}"
    inner_params = mb.params + wb.params
    params = list(inner_params)

    if spec.analysis == Analysis.change:
        lag = f"LAG(m0) OVER ({'PARTITION BY d1 ' if len(dims) > 1 else ''}ORDER BY d0)"
        tmpl = f"SELECT *, m0 - {lag} AS change, (m0 - {lag}) / NULLIF(ABS({lag}), 0) AS change_pct FROM ({{inner}}) q"
        outer_q, outer_d = tmpl.format(inner=inner_q), tmpl.format(inner=inner_d)
    elif spec.analysis == Analysis.share:
        tmpl = "SELECT *, m0 / NULLIF(SUM(m0) OVER (), 0) AS share FROM ({inner}) q"
        outer_q, outer_d = tmpl.format(inner=inner_q), tmpl.format(inner=inner_d)
    elif spec.analysis == Analysis.anomaly:
        d0, m0 = dims[0], spec.measures[0]
        series_q, series_d = inner_q, inner_d
        if d0.grain is not None and m0.aggregation in (Aggregation.count_rows, Aggregation.count_distinct, Aggregation.sum):
            # periods without rows are real zeros for counts and sums: generate every period so they can be judged
            x, g, step = ident(d0.column), d0.grain.value, GRAIN_INTERVAL[d0.grain]
            span = "SELECT date_trunc('{g}', MIN({x})) AS lo, date_trunc('{g}', MAX({x})) AS hi FROM {t}{w}"
            gen = "SELECT range AS d0 FROM ({span}) s, range(s.lo, s.hi + {step}, {step})"
            fill = "SELECT p.d0, COALESCE(q.m0, 0) AS m0 FROM ({gen}) p LEFT JOIN ({inner}) q USING (d0)"
            series_q = fill.format(gen=gen.format(span=span.format(g=g, x=x, t=table, w=wq), step=step), inner=inner_q)
            series_d = fill.format(gen=gen.format(span=span.format(g=g, x=x, t=table, w=wd), step=step), inner=inner_d)
            params = list(wb.params) + inner_params
        # robust z = (x − median) / scale; scale = 1.4826·MAD, or 1.2533·mean absolute deviation when
        # MAD is 0 (sparse series where most periods equal the median)
        tmpl = ("WITH s AS ({series}), st AS (SELECT MEDIAN(m0) AS med FROM s), "
                "dv AS (SELECT COALESCE(NULLIF(1.4826 * MEDIAN(ABS(s.m0 - st.med)), 0), "
                "NULLIF(1.2533 * AVG(ABS(s.m0 - st.med)), 0)) AS scale FROM s, st) "
                "SELECT s.d0, s.m0, st.med AS typical, (s.m0 - st.med) / dv.scale AS z, "
                f"COALESCE(ABS((s.m0 - st.med) / dv.scale) >= {ANOMALY_Z}, FALSE) AS anomaly "
                "FROM s, st, dv")
        outer_q, outer_d = tmpl.format(series=series_q), tmpl.format(series=series_d)
    else:
        outer_q, outer_d = inner_q, inner_d

    order = _order(spec, ctx)
    params.append(spec.limit + 1)  # one extra row tells us whether the result was truncated
    return f"{outer_q}{order} LIMIT ?", params, f"{outer_d}{order} LIMIT {spec.limit}"


def where_clause(conds: list[Condition], ctx: DatasetContext) -> tuple[str, list[Any]]:
    b = _Sql()
    parts = [_cond(c, ctx, b)[0] for c in conds]
    return (f" WHERE {' AND '.join(parts)}" if parts else ""), b.params


def _order(spec: QuerySpec, ctx: DatasetContext) -> str:
    dims = spec.dimensions
    if spec.analysis == Analysis.anomaly:
        return " ORDER BY d0"
    if spec.sort is not None:
        key = f"m{spec.sort.index}" if spec.sort.by == "measure" else f"d{spec.sort.index}"
        tie = ", d0 ASC NULLS LAST" if dims and key != "d0" else ""
        return f" ORDER BY {key} {spec.sort.direction.upper()} NULLS LAST{tie}"
    if not dims:
        return ""
    if _is_time(dims[0], ctx):
        return " ORDER BY d0 ASC NULLS LAST" + (", d1 ASC NULLS LAST" if len(dims) > 1 else "")
    return " ORDER BY m0 DESC NULLS LAST, d0 ASC NULLS LAST"


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


class ResultColumn(BaseModel):
    key: str
    label: str
    kind: str  # dimension | measure | change | change_pct | share | typical | z | anomaly
    type: str  # logical type of the values (text, date, integer, number, duration, percent, boolean…)
    format: str  # number | lak | duration | percent | date | text | boolean
    grain: str | None = None
    column: str | None = None  # source column key (dimensions) — used for cross-filtering


class QueryResult(BaseModel):
    spec: QuerySpec
    description: str
    columns: list[ResultColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    matched_rows: int
    total_rows: int
    sql: str
    dataset_id: int
    dataset_name: str
    dataset_version: int
    computed_at: dt.datetime
    elapsed_ms: int


def measure_label(m: Measure, ctx: DatasetContext) -> str:
    if m.label:
        return m.label
    col = ctx.col(m.column) if m.column else None
    if m.aggregation == Aggregation.count_rows:
        base = "Records"
    elif m.aggregation == Aggregation.share_of_rows:
        base = "Share of records"
    else:
        base = f"{AGG_WORD[m.aggregation]} {leaf_label(col['label']) if col else m.column}"
    if m.filter is not None:
        base += f" ({describe_condition(m.filter, ctx)})"
    return base


def measure_format(m: Measure, ctx: DatasetContext) -> tuple[str, str]:
    """(format, type) of a measure's values."""
    a = m.aggregation
    if a == Aggregation.share_of_rows:
        return "percent", "number"
    if a in (Aggregation.count_rows, Aggregation.count_distinct) or not m.column:
        return "number", "integer"
    col = ctx.col(m.column) or {}
    t = col.get("logical_type")
    if t in TEMPORAL:
        return "date", t
    if t == "duration":
        return "duration", "duration"
    if col.get("unit") == "LAK":
        return "lak", "number"
    if col.get("unit") == "percent":
        return "percent", "number"
    return "number", "number" if a == Aggregation.average else (t or "number")


def _fmt_value(v: Any) -> str:
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def describe_condition(c: Condition, ctx: DatasetContext) -> str:
    col = dict(ctx.col(c.column) or {"label": c.column})
    col["label"] = leaf_label(col["label"])
    vals = [_fmt_value(v) for v in c.values]
    if c.op in NO_VALUE_OPS:
        return f"{col['label']} {OP_WORD[c.op]}"
    if c.op == Op.between:
        return f"{col['label']} between {vals[0]} and {vals[1]}"
    if c.op in (Op.in_, Op.not_in):
        return f"{col['label']} {OP_WORD[c.op]} {', '.join(vals)}"
    return f"{col['label']} {OP_WORD[c.op]} {vals[0]}"


def describe(spec: QuerySpec, ctx: DatasetContext) -> str:
    """Plain-language statement of exactly what is computed (generated from the validated spec)."""
    parts = [" and ".join(measure_label(m, ctx) for m in spec.measures)]
    if spec.dimensions:
        ds = []
        for d in spec.dimensions:
            label = leaf_label((ctx.col(d.column) or {"label": d.column})["label"])
            ds.append(f"{d.grain.value} of {label}" if d.grain else label)
        parts.append("by " + " and ".join(ds))
    if spec.filters:
        parts.append("where " + "; ".join(describe_condition(c, ctx) for c in spec.filters))
    s = " ".join(parts)
    if spec.analysis == Analysis.change:
        s += ", with change from the previous period"
    elif spec.analysis == Analysis.share:
        s += ", with each group's share of the total"
    elif spec.analysis == Analysis.anomaly:
        s += f", flagging unusual periods (robust z-score ≥ {ANOMALY_Z})"
    if spec.sort is not None:
        target = measure_label(spec.measures[spec.sort.index], ctx) if spec.sort.by == "measure" else "grouping"
        s += f"; sorted by {target} ({'highest' if spec.sort.direction == 'desc' else 'lowest'} first)"
    if spec.dimensions and spec.limit < 1000:
        s += f"; at most {spec.limit} rows"
    return s + "."


def _columns(spec: QuerySpec, ctx: DatasetContext) -> list[ResultColumn]:
    out = []
    for i, d in enumerate(spec.dimensions):
        col = ctx.col(d.column) or {}
        t = col.get("logical_type", "text")
        fmt = "date" if t in TEMPORAL else ("duration" if t == "duration" else ("lak" if col.get("unit") == "LAK" else
                                                                             ("number" if t in NUMERIC else "text")))
        lab = leaf_label(col.get("label", d.column))
        out.append(ResultColumn(key=f"d{i}", label=(f"{lab} ({d.grain.value})" if d.grain else lab),
                                kind="dimension", type=t, format=fmt, grain=d.grain.value if d.grain else None, column=d.column))
    if spec.analysis == Analysis.anomaly:
        f, t = measure_format(spec.measures[0], ctx)
        lab = measure_label(spec.measures[0], ctx)
        return out + [ResultColumn(key="m0", label=lab, kind="measure", type=t, format=f),
                      ResultColumn(key="typical", label="Typical (median)", kind="typical", type="number", format=f),
                      ResultColumn(key="z", label="Robust z-score", kind="z", type="number", format="number"),
                      ResultColumn(key="anomaly", label="Unusual", kind="anomaly", type="boolean", format="boolean")]
    for i, m in enumerate(spec.measures):
        f, t = measure_format(m, ctx)
        out.append(ResultColumn(key=f"m{i}", label=measure_label(m, ctx), kind="measure", type=t, format=f))
    if spec.analysis == Analysis.change:
        f, t = measure_format(spec.measures[0], ctx)
        out += [ResultColumn(key="change", label="Change", kind="change", type=t, format=f),
                ResultColumn(key="change_pct", label="Change %", kind="change_pct", type="number", format="percent")]
    elif spec.analysis == Analysis.share:
        out.append(ResultColumn(key="share", label="Share of total", kind="share", type="number", format="percent"))
    return out


def _json(v: Any, temporal_date: bool) -> Any:
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        ts = pd.Timestamp(v)
        return ts.date().isoformat() if temporal_date and ts.time() == dt.time(0) else ts.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, (bool, int, str)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.item() if hasattr(v, "item") else str(v)


def run_query(spec: QuerySpec, ctx: DatasetContext) -> QueryResult:
    t0 = time.perf_counter()
    spec = validate(spec, ctx)
    sql, params, disp = compile_query(spec, ctx)
    cols = _columns(spec, ctx)
    con = duckdb.connect(config={"enable_external_access": False})
    try:
        con.register("data", ctx.df[[c["key"] for c in ctx.columns]])
        rows = con.execute(sql, params).fetchall()
        w, wp = where_clause(spec.filters, ctx)
        matched = con.execute(f"SELECT COUNT(*) FROM data{w}", wp).fetchone()[0]
    except duckdb.Error as e:  # a validated spec should never fail; report it rather than crash
        raise QueryInvalid(f"the query could not be executed ({type(e).__name__}: {str(e)[:200]})") from e
    finally:
        con.close()
    truncated = len(rows) > spec.limit
    rows = rows[: spec.limit]
    temporal_date = [c.type == "date" or c.grain is not None for c in cols]
    out_rows = [[_json(v, temporal_date[i] if i < len(temporal_date) else False) for i, v in enumerate(r)] for r in rows]
    return QueryResult(spec=spec, description=describe(spec, ctx), columns=cols, rows=out_rows, row_count=len(out_rows),
                       truncated=truncated, matched_rows=int(matched), total_rows=len(ctx.df), sql=disp,
                       dataset_id=ctx.dataset_id, dataset_name=ctx.name, dataset_version=ctx.version,
                       computed_at=dt.datetime.now(dt.UTC), elapsed_ms=int((time.perf_counter() - t0) * 1000))
