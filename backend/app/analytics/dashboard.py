"""Dashboards: panels are validated QuerySpecs, generated from the Blueprint's dashboard plan and
edited by users. Filters (filter bar and cross-filtering) are extra conditions merged into each
panel's query, so every number on a dashboard is still produced by inspectable SQL."""

from __future__ import annotations

import datetime as dt
import io
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.engine import NUMERIC, TEMPORAL, QueryInvalid, QueryResult, run_query, validate
from app.analytics.nl import _role, compose_answer
from app.analytics.spec import Analysis, Condition, QuerySpec, TimeGrain
from app.blueprint.context import DatasetContext
from app.blueprint.rules import leaf_label, rules_blueprint
from app.blueprint.schema import Aggregation, Blueprint, ChartKind, Kpi, SemanticRole

MAX_PANELS = 24


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashPanel(_S):
    id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    chart: ChartKind
    query: QuerySpec
    width: int = Field(default=6, ge=2, le=12)  # columns of a 12-column grid
    note: str | None = Field(default=None, max_length=300)


class FilterDef(_S):
    dataset_id: int
    column: str = Field(max_length=200)
    label: str = Field(max_length=120)


class DashboardContent(_S):
    panels: list[DashPanel] = Field(default_factory=list, max_length=MAX_PANELS)
    filters: list[FilterDef] = Field(default_factory=list, max_length=8)


class ActiveFilter(_S):
    """A filter applied while viewing: from the filter bar, or a cross-filter from a panel click
    (``source_panel`` keeps that panel unfiltered so the selection stays visible in context)."""

    dataset_id: int
    condition: Condition
    source_panel: str | None = Field(default=None, max_length=40)


class PanelData(BaseModel):
    panel_id: str
    result: QueryResult | None = None
    summary: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


def _kpi_measure(k: Kpi) -> dict[str, Any]:
    flt = None
    if k.filter is not None:
        flt = {"column": k.filter.column, "op": "in" if len(k.filter.values) > 1 else "eq", "values": list(k.filter.values)}
    return {"aggregation": k.aggregation, "column": k.column, "filter": flt, "label": k.name}


def _roles(bp: Blueprint, ctx: DatasetContext) -> dict[str, SemanticRole]:
    plan = next((p for p in bp.datasets if p.dataset_id == ctx.dataset_id), None)
    if plan is None:
        return {c["key"]: r for c in ctx.columns if (r := _role(c)) is not None}
    return {c.key: c.semantic_role for c in plan.columns if c.status != "rejected"}


def generate_content(contexts: list[DatasetContext], bp: Blueprint | None, source_id: int) -> tuple[DashboardContent, str, list[str]]:
    """Build a dashboard from the Blueprint (or from a fresh rule-based plan). Returns
    (content, origin, notes). Every panel is validated against the data; invalid ones are left out
    and listed in the notes."""
    origin = "blueprint"
    if bp is None:
        bp = rules_blueprint(source_id, contexts)
        origin = "rules"
    by_id = {c.dataset_id: c for c in contexts}
    notes: list[str] = []
    panels: list[DashPanel] = []

    def add(ctx: DatasetContext, title: str, chart: ChartKind, spec: dict[str, Any], width: int, note: str | None = None) -> None:
        pid = f"p{len(panels) + 1}"
        try:
            q = validate(QuerySpec.model_validate({"dataset_id": ctx.dataset_id, **spec}), ctx)
        except (QueryInvalid, ValueError) as e:
            notes.append(f"Panel '{title}' left out: {e}")
            return
        panels.append(DashPanel(id=pid, title=title[:120], chart=chart, query=q, width=width, note=note))

    primary = max(contexts, key=lambda c: len(c.df)) if contexts else None
    if primary is None:
        return DashboardContent(), origin, ["The source has no datasets."]
    plan = next((p for p in bp.datasets if p.dataset_id == primary.dataset_id), None)
    kpis = [k for k in (plan.kpis if plan else []) if k.status != "rejected"]
    roles = _roles(bp, primary)
    label = {c.key: c.display_name for c in plan.columns} if plan else {c["key"]: leaf_label(c["label"]) for c in primary.columns}
    date_key = next((k for k, r in roles.items() if r in (SemanticRole.timestamp, SemanticRole.date)
                     and (primary.col(k) or {}).get("logical_type") in TEMPORAL), None)
    amount = next((k for k in kpis if k.aggregation == Aggregation.sum and k.filter is None), None)
    base = _kpi_measure(amount) if amount else {"aggregation": "count_rows", "label": "Records"}

    # 1. key figures
    for k in kpis[:4]:
        add(primary, k.name, ChartKind.kpi_card, {"measures": [_kpi_measure(k)]}, 3, note=k.description or None)

    # 2. the Blueprint's planned panels
    for p in bp.dashboard:
        if p.status == "rejected" or p.chart == ChartKind.kpi_card:
            continue
        ctx = by_id.get(p.dataset_id)
        if ctx is None:
            continue
        pplan = next((d for d in bp.datasets if d.dataset_id == p.dataset_id), None)
        pk = [k for k in (pplan.kpis if pplan else []) if k.id in p.kpi_ids and k.status != "rejected"]
        m = _kpi_measure(pk[0]) if pk else {"aggregation": "count_rows", "label": "Records"}
        if p.chart == ChartKind.line and p.time_column:
            add(ctx, p.title, ChartKind.line, {"measures": [m], "dimensions": [{"column": p.time_column, "grain": "month"}],
                                               "analysis": "change"}, 6)
        elif p.chart == ChartKind.donut and p.dimension:
            add(ctx, p.title, ChartKind.donut, {"measures": [{"aggregation": "count_rows", "label": "Records"}],
                                                "dimensions": [{"column": p.dimension}], "analysis": "share"}, 6)
        elif p.dimension:
            add(ctx, p.title, ChartKind.bar, {"measures": [m], "dimensions": [{"column": p.dimension}], "limit": 20}, 6)

    # 3. coverage the plan may lack: ranking by person, unusual days
    person = next((k for k, r in roles.items() if r == SemanticRole.person and (primary.col(k) or {}).get("logical_type") == "text"), None)
    if person and not any(d.column == person for pnl in panels for d in pnl.query.dimensions):
        add(primary, f"{base['label']} by {label.get(person, person)}", ChartKind.bar,
            {"measures": [base], "dimensions": [{"column": person}], "sort": {"by": "measure", "direction": "desc"}, "limit": 15}, 6)
    if date_key:
        add(primary, "Unusual days", ChartKind.line, {"measures": [{"aggregation": "count_rows", "label": "Records"}],
                                                      "dimensions": [{"column": date_key, "grain": "day"}], "analysis": "anomaly",
                                                      "limit": 1000}, 12,
            note="Days whose record count is far from the typical day (robust z-score ≥ 3.5).")

    # filter bar: the date column and up to three low-cardinality categories
    filters: list[FilterDef] = []
    if date_key:
        filters.append(FilterDef(dataset_id=primary.dataset_id, column=date_key, label=label.get(date_key, date_key)))
    for k, r in roles.items():
        col = primary.col(k) or {}
        if (r in (SemanticRole.organization_unit, SemanticRole.person, SemanticRole.category, SemanticRole.status,
                  SemanticRole.location) and col.get("logical_type") == "text"
                and (primary.prof(k).get("distinct") or 999) <= 60 and len(filters) < 4):
            filters.append(FilterDef(dataset_id=primary.dataset_id, column=k, label=label.get(k, k)))
    return DashboardContent(panels=panels, filters=filters), origin, notes


def validate_content(content: DashboardContent, contexts: dict[int, DatasetContext]) -> DashboardContent:
    """Validate every panel query and filter against the data (used on every edit)."""
    ids = [p.id for p in content.panels]
    if len(set(ids)) != len(ids):
        raise QueryInvalid("panel ids must be unique")
    panels = []
    for p in content.panels:
        ctx = contexts.get(p.query.dataset_id)
        if ctx is None:
            raise QueryInvalid(f"panel '{p.title}': dataset {p.query.dataset_id} is not part of this source")
        try:
            panels.append(p.model_copy(update={"query": validate(p.query, ctx)}))
        except QueryInvalid as e:
            raise QueryInvalid(f"panel '{p.title}': {e}") from e
    for f in content.filters:
        ctx = contexts.get(f.dataset_id)
        if ctx is None or ctx.col(f.column) is None:
            raise QueryInvalid(f"filter '{f.label}' refers to an unknown column")
    return content.model_copy(update={"panels": panels})


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def apply_filters(p: DashPanel, active: list[ActiveFilter], ctx: DatasetContext) -> QuerySpec:
    extra = [f.condition for f in active
             if f.dataset_id == p.query.dataset_id and f.source_panel != p.id and ctx.col(f.condition.column) is not None]
    return p.query.model_copy(update={"filters": [*p.query.filters, *extra][:12]})


def evaluate(content: DashboardContent, contexts: dict[int, DatasetContext], active: list[ActiveFilter]) -> list[PanelData]:
    out = []
    for p in content.panels:
        ctx = contexts.get(p.query.dataset_id)
        if ctx is None:
            out.append(PanelData(panel_id=p.id, error="The dataset of this panel is no longer available."))
            continue
        try:
            res = run_query(apply_filters(p, active, ctx), ctx)
            out.append(PanelData(panel_id=p.id, result=res, summary=compose_answer(res)))
        except QueryInvalid as e:
            out.append(PanelData(panel_id=p.id, error=str(e)))
    return out


def filter_options(content: DashboardContent, contexts: dict[int, DatasetContext]) -> list[dict[str, Any]]:
    out = []
    for f in content.filters:
        ctx = contexts.get(f.dataset_id)
        col = ctx.col(f.column) if ctx else None
        if ctx is None or col is None:
            continue
        s = ctx.df[f.column]
        item: dict[str, Any] = {"dataset_id": f.dataset_id, "column": f.column, "label": f.label, "type": col["logical_type"]}
        if col["logical_type"] in TEMPORAL:
            nn = s.dropna()
            item["min"] = nn.min().date().isoformat() if len(nn) else None
            item["max"] = nn.max().date().isoformat() if len(nn) else None
        elif col["logical_type"] in NUMERIC:
            item["min"], item["max"] = (float(s.min()), float(s.max())) if s.notna().any() else (None, None)
        else:
            vc = s.dropna().astype(str).value_counts().head(200)
            item["values"] = [{"value": v, "count": int(n)} for v, n in vc.items()]
        out.append(item)
    return out


def insights(ctx: DatasetContext, roles: dict[str, SemanticRole] | None = None) -> list[dict[str, Any]]:
    """Deterministic findings, each produced by a query whose SQL is returned with it."""
    roles = roles or {c["key"]: r for c in ctx.columns if (r := _role(c)) is not None}
    date_key = next((c["key"] for c in ctx.columns if c["logical_type"] in TEMPORAL), None)
    amount = next((c["key"] for c in ctx.columns if c.get("unit") == "LAK"), None)
    unit = next((k for k, r in roles.items() if r == SemanticRole.organization_unit and (ctx.col(k) or {}).get("logical_type") == "text"), None)
    cat = next((k for k, r in roles.items() if r in (SemanticRole.category, SemanticRole.status)
                and (ctx.col(k) or {}).get("logical_type") == "text" and k != unit), None)
    specs: list[tuple[str, str, dict[str, Any]]] = []
    if date_key and ctx.df[date_key].dropna().dt.to_period("M").nunique() >= 2:
        specs.append(("trend", "Monthly trend", {"measures": [{"aggregation": "count_rows", "label": "Records"}],
                                                 "dimensions": [{"column": date_key, "grain": TimeGrain.month}], "analysis": Analysis.change}))
    if unit:
        m = {"aggregation": "sum", "column": amount} if amount else {"aggregation": "count_rows", "label": "Records"}
        specs.append(("ranking", f"Leading {leaf_label((ctx.col(unit) or {})['label'])}",
                      {"measures": [m], "dimensions": [{"column": unit}], "sort": {"by": "measure", "direction": "desc"}, "limit": 5}))
    if cat:
        specs.append(("share", f"{leaf_label((ctx.col(cat) or {})['label'])} mix",
                      {"measures": [{"aggregation": "count_rows", "label": "Records"}], "dimensions": [{"column": cat}],
                       "analysis": Analysis.share, "limit": 8}))
    if date_key:
        specs.append(("anomaly", "Unusual days", {"measures": [{"aggregation": "count_rows", "label": "Records"}],
                                                  "dimensions": [{"column": date_key, "grain": TimeGrain.day}],
                                                  "analysis": Analysis.anomaly, "limit": 1000}))
    out = []
    for kind, title, spec in specs:
        try:
            res = run_query(QuerySpec.model_validate({"dataset_id": ctx.dataset_id, **spec}), ctx)
        except QueryInvalid:
            continue
        out.append({"kind": kind, "title": title, "text": compose_answer(res), "sql": res.sql,
                    "spec": res.spec.model_dump(mode="json"), "dataset_version": res.dataset_version})
    return out


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

_XL_FMT = {"lak": '#,##0 "₭"', "number": "#,##0.##", "percent": "0.0%", "duration": "[h]:mm:ss", "date": "yyyy-mm-dd"}


def export_xlsx(name: str, panels: list[DashPanel], data: list[PanelData], active: list[ActiveFilter],
                contexts: dict[int, DatasetContext], tz: str = "Asia/Vientiane") -> bytes:
    import xlsxwriter

    from app.excel.builder import excel_serial

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False, "strings_to_formulas": False})
    font = {"font_name": "Noto Sans Lao", "font_size": 10}
    h1 = wb.add_format({**font, "bold": True, "font_size": 16})
    bold = wb.add_format({**font, "bold": True})
    head = wb.add_format({**font, "bold": True, "bg_color": "#1B2238", "font_color": "#F2F4FA", "border": 1})
    txt = wb.add_format({**font, "text_wrap": True, "valign": "top"})
    mono = wb.add_format({"font_name": "Consolas", "font_size": 9, "text_wrap": True, "valign": "top", "font_color": "#334"})
    fmts = {k: wb.add_format({**font, "num_format": v}) for k, v in _XL_FMT.items()}
    plain = wb.add_format(font)
    link = wb.add_format({**font, "font_color": "#2A6BD6", "underline": 1})

    now = dt.datetime.now(ZoneInfo(tz))
    ws = wb.add_worksheet("Dashboard")
    ws.set_column(0, 0, 34)
    ws.set_column(1, 1, 90)
    ws.write(0, 0, name, h1)
    ws.write(1, 0, f"Exported {now:%Y-%m-%d %H:%M} ({tz}) by PRISM", txt)
    versions = ", ".join(f"{c.name} v{c.version}" for c in contexts.values())
    ws.write(2, 0, f"Data: {versions}", txt)
    filt = "; ".join(describe_active(f, contexts) for f in active) or "none"
    ws.write(3, 0, f"Filters applied: {filt}", txt)
    ws.write_row(5, 0, ["Panel", "Result"], head)
    used: set[str] = set()
    by_id = {d.panel_id: d for d in data}
    for i, p in enumerate(panels, start=1):
        d = by_id.get(p.id)
        sheet = _sheet_name(f"{i} {p.title}", used)
        ws.write_url(5 + i, 0, f"internal:'{sheet}'!A1", link, p.title)
        ws.write(5 + i, 1, (d.summary if d and d.summary else (d.error if d else "")) or "", txt)
        ps = wb.add_worksheet(sheet)
        ps.write(0, 0, p.title, h1)
        if d is None or d.result is None:
            ps.write(2, 0, f"Not computed: {d.error if d else 'unknown error'}", txt)
            continue
        r = d.result
        ps.write(1, 0, r.description, txt)
        ps.write(2, 0, d.summary or "", txt)
        ps.write_row(4, 0, [c.label for c in r.columns], head)
        for ci, c in enumerate(r.columns):
            ps.set_column(ci, ci, 22 if c.kind == "dimension" else 16)
        for ri, row in enumerate(r.rows, start=5):
            for ci, (c, v) in enumerate(zip(r.columns, row)):
                if v is None:
                    continue
                if c.format == "date" and isinstance(v, str):
                    try:
                        ps.write_number(ri, ci, excel_serial(dt.datetime.fromisoformat(v)), fmts["date"])
                        continue
                    except ValueError:
                        pass
                if c.format == "duration" and isinstance(v, (int, float)):
                    ps.write_number(ri, ci, v / 86400, fmts["duration"])
                elif isinstance(v, bool):
                    ps.write(ri, ci, "yes" if v else "no", plain)
                elif isinstance(v, (int, float)):
                    ps.write_number(ri, ci, v, fmts.get(c.format, fmts["number"]))
                else:
                    ps.write_string(ri, ci, str(v), plain)
        end = 5 + len(r.rows) + 1
        ps.write(end, 0, "Provenance", bold)
        ps.write(end + 1, 0, f"Dataset: {r.dataset_name} · version {r.dataset_version} · {r.matched_rows:,} of {r.total_rows:,} records "
                             f"matched · computed {r.computed_at.astimezone(ZoneInfo(tz)):%Y-%m-%d %H:%M}", txt)
        ps.merge_range(end + 2, 0, end + 8, max(3, len(r.columns) - 1), r.sql, mono)
    wb.close()
    return buf.getvalue()


def _sheet_name(base: str, used: set[str]) -> str:
    clean = "".join(ch for ch in base if ch not in "[]:*?/\\'")[:31].strip() or "Panel"
    name, n = clean, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name, n = clean[: 31 - len(suffix)] + suffix, n + 1
    used.add(name.lower())
    return name


def describe_active(f: ActiveFilter, contexts: dict[int, DatasetContext]) -> str:
    from app.analytics.engine import describe_condition

    ctx = contexts.get(f.dataset_id)
    return describe_condition(f.condition, ctx) if ctx else f.condition.column
