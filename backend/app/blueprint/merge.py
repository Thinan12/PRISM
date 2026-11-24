"""Merge a validated AI interpretation into the rule-based blueprint.

Every AI proposal is checked against the data: unknown columns, impossible aggregations,
values that do not occur, unmeasured relationships and duplicate names are dropped or
corrected, and each drop is recorded as a warning. Deterministic facts (types, profiles,
duplicate counts, measured overlaps) always come from PRISM, never from the model.
"""

from __future__ import annotations

import re

from app.blueprint.ai import AIInterpretation
from app.blueprint.context import DatasetContext
from app.blueprint.kpi import KpiInvalid, natural_format, validate_kpi
from app.blueprint.schema import (
    Aggregation,
    Assumption,
    Blueprint,
    Domain,
    Filter,
    Kpi,
    OpExcludeColumn,
    OpMapValues,
    Operation,
    Panel,
    Relationship,
    ValueMapping,
)


_LAO_RUN = re.compile(r"[຀-໿]+")  # Lao Unicode block


def ungrounded_lao(name: str, *sources: str | None) -> list[str]:
    """Lao words in an AI-proposed name that do not occur in the source header text. Models
    (especially small local ones) can produce plausible-looking but wrong Lao; PRISM only accepts
    Lao text that the data itself contains."""
    src = " ".join(s for s in sources if s)
    return [w for w in _LAO_RUN.findall(name) if w not in src]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def merge_ai(bp: Blueprint, ai: AIInterpretation, contexts: list[DatasetContext]) -> tuple[Blueprint, list[str]]:
    bp = bp.model_copy(deep=True)
    warnings: list[str] = []
    ctx_by_id = {c.dataset_id: c for c in contexts}
    bp.domain = Domain(label=ai.domain.label[:120] or bp.domain.label, confidence=_clamp(ai.domain.confidence),
                       rationale=ai.domain.rationale[:600], origin="ai")

    def ref(r: str) -> int | None:
        try:
            v = int(r)
        except (TypeError, ValueError):
            return None
        return v if v in ctx_by_id else None

    op_seq = sum(len(d.operations) for d in bp.datasets)
    for ads in ai.datasets:
        ds_id = ref(ads.dataset_ref)
        if ds_id is None:
            warnings.append(f"AI referenced unknown dataset '{ads.dataset_ref}'; ignored.")
            continue
        ctx = ctx_by_id[ds_id]
        plan = bp.dataset(ds_id)
        if ads.title.strip():
            plan.title = ads.title.strip()[:200]
        if ads.summary.strip():
            plan.summary = ads.summary.strip()[:1000]

        # columns
        cols = {c.key: c for c in plan.columns}
        for ac in ads.columns:
            cp = cols.get(ac.key)
            if cp is None:
                warnings.append(f"AI described unknown column '{ac.key}'; ignored.")
                continue
            name = " ".join(ac.display_name.split())[:80] or cp.display_name
            bad = ungrounded_lao(name, cp.original_label, cp.display_name)
            if bad:
                warnings.append(f"AI name '{name}' for '{cp.display_name}' kept out: Lao text "
                                f"{', '.join(repr(b) for b in bad)} does not appear in the source header.")
                name = cp.display_name
            cp.display_name = name
            cp.description = ac.description[:400]
            cp.semantic_role = ac.semantic_role
            cp.confidence = _clamp(ac.confidence)
            cp.rationale = ac.rationale[:400]
            cp.origin = "ai"
            cp.status = "approved" if name == cp.original_label else "proposed"
        seen: dict[str, int] = {}
        for cp in plan.columns:  # enforce unique display names deterministically
            k = cp.display_name.casefold()
            if k in seen:
                seen[k] += 1
                cp.display_name = f"{cp.display_name[:74]} ({seen[k]})"
                warnings.append(f"Duplicate display name made unique: '{cp.display_name}'.")
            else:
                seen[k] = 1

        # KPIs
        existing = {(k.aggregation, k.column, tuple(k.filter.values) if k.filter else None) for k in plan.kpis}
        for i, ak in enumerate(ads.kpis[:12], start=1):
            if ak.filter_column and not ak.filter_values:
                warnings.append(f"AI KPI '{ak.name}' dropped: it filters on '{ak.filter_column}' without values; "
                                "a KPI is one number — breakdowns belong in dashboard panels.")
                continue
            flt = Filter(column=ak.filter_column, values=ak.filter_values) if ak.filter_column and ak.filter_values else None
            k = Kpi(id=f"k{ds_id}-ai{i}", name=ak.name.strip()[:80] or f"KPI {i}", description=ak.description[:300],
                    aggregation=ak.aggregation, column=ak.column if ak.aggregation != Aggregation.count_rows else None,
                    filter=flt, confidence=_clamp(ak.confidence), rationale=ak.rationale[:400], origin="ai")
            try:
                validate_kpi(k, ctx)
            except KpiInvalid as e:
                warnings.append(f"AI KPI '{ak.name}' dropped: {e}.")
                continue
            sig = (k.aggregation, k.column, tuple(k.filter.values) if k.filter else None)
            dup = next((x for x in plan.kpis if (x.aggregation, x.column, tuple(x.filter.values) if x.filter else None) == sig), None)
            if dup is not None:  # same computation: adopt the AI's naming, keep one KPI
                dup.name, dup.description, dup.origin = k.name, k.description or dup.description, "ai"
                dup.confidence = max(dup.confidence, k.confidence)
                continue
            if sig in existing:
                continue
            k.format = natural_format(k, ctx)
            plan.kpis.append(k)
            existing.add(sig)

        # value mappings — variants must occur in the data exactly
        for am in ads.value_mappings:
            col = ctx.col(am.column)
            if col is None or col["logical_type"] != "text":
                warnings.append(f"AI value mapping on '{am.column}' dropped: not a text column.")
                continue
            present = ctx.distinct_values(am.column)
            variants = [v for v in dict.fromkeys(am.variants) if v in present and v != am.canonical]
            missing = [v for v in am.variants if v not in present and v != am.canonical]
            if missing:
                warnings.append(f"AI mapping for '{col['label']}' referenced values not in the data: {missing[:5]}.")
            if not variants or not am.canonical.strip():
                continue
            counts = ctx.df[am.column].astype("string").value_counts()
            vm = ValueMapping(to=am.canonical.strip()[:200], variants=variants, rows_affected=int(sum(counts.get(v, 0) for v in variants)))
            # if a rules op already maps the same variants, the AI's version replaces it
            for op in list(plan.operations):
                if isinstance(op.spec, OpMapValues) and op.spec.column == am.column:
                    rem = [m for m in op.spec.mappings if not set(m.variants) & set(variants)]
                    if len(rem) != len(op.spec.mappings):
                        if rem:
                            op.spec.mappings = rem
                        else:
                            plan.operations.remove(op)
            op_seq += 1
            plan.operations.append(Operation(
                id=f"op{op_seq}", spec=OpMapValues(column=am.column, mappings=[vm]), confidence=_clamp(am.confidence),
                origin="ai", title=f"Map {' / '.join(variants[:3])} → {vm.to} in {col['label']}"[:200], rationale=am.rationale[:600],
            ))
        for ex in ads.exclude_columns:
            col = ctx.col(ex.column)
            if col is None:
                warnings.append(f"AI exclusion of unknown column '{ex.column}' dropped.")
                continue
            if any(isinstance(o.spec, OpExcludeColumn) and o.spec.column == ex.column for o in plan.operations):
                continue
            op_seq += 1
            plan.operations.append(Operation(
                id=f"op{op_seq}", spec=OpExcludeColumn(column=ex.column), confidence=_clamp(ex.confidence), origin="ai",
                title=f"Exclude column {col['label']}"[:200], rationale=ex.rationale[:600],
            ))

    # relationships — keep only those whose overlap PRISM can measure
    known = {(r.from_dataset, r.from_column, r.to_dataset, r.to_column) for r in bp.relationships}
    for ar in ai.relationships:
        a, b = ref(ar.from_dataset), ref(ar.to_dataset)
        if a is None or b is None or ctx_by_id[a].col(ar.from_column) is None or ctx_by_id[b].col(ar.to_column) is None:
            warnings.append(f"AI relationship {ar.from_dataset}.{ar.from_column} → {ar.to_dataset}.{ar.to_column} references unknown columns; dropped.")
            continue
        key = (a, ar.from_column, b, ar.to_column)
        if key in known:
            r = next(r for r in bp.relationships if (r.from_dataset, r.from_column, r.to_dataset, r.to_column) == key)
            r.rationale = (r.rationale + " AI: " + ar.rationale)[:400]
            continue
        va, vb = ctx_by_id[a].distinct_values(ar.from_column), ctx_by_id[b].distinct_values(ar.to_column)
        ratio = len(va & vb) / len(va) if va else 0.0
        if ratio < 0.5:
            warnings.append(f"AI relationship {ar.from_column} → {ar.to_column} dropped: only {ratio:.0%} of values match.")
            continue
        bp.relationships.append(Relationship(
            id=f"r{len(bp.relationships) + 1}", from_dataset=a, from_column=ar.from_column, to_dataset=b, to_column=ar.to_column,
            match_ratio=round(ratio, 4), verified=ratio >= 0.8, rationale=ar.rationale[:400],
            confidence=min(_clamp(ar.confidence), ratio), origin="ai",
        ))

    # dashboard — panels must reference existing KPIs/columns
    if ai.dashboard:
        panels = []
        for i, ap in enumerate(ai.dashboard[:8], start=1):
            ds_id = ref(ap.dataset_ref)
            if ds_id is None:
                continue
            plan, ctx = bp.dataset(ds_id), ctx_by_id[ds_id]
            by_name = {k.name.casefold(): k.id for k in plan.kpis}
            kpi_ids = [by_name[n.casefold()] for n in ap.kpi_names if n.casefold() in by_name]
            dim = ap.dimension_column if ap.dimension_column and ctx.col(ap.dimension_column) else None
            tcol = ap.time_column if ap.time_column and (ctx.col(ap.time_column) or {}).get("logical_type") in ("date", "datetime") else None
            if not kpi_ids and not dim:
                warnings.append(f"AI panel '{ap.title}' dropped: no valid KPI or column.")
                continue
            panels.append(Panel(id=f"p{i}", title=ap.title[:120], chart=ap.chart, dataset_id=ds_id, kpi_ids=kpi_ids,
                                dimension=dim, time_column=tcol, origin="ai"))
        if panels:
            bp.dashboard = panels

    for a in ai.assumptions[:20]:
        bp.assumptions.append(Assumption(id=f"a{len(bp.assumptions) + 1}", text=a.text[:600], confidence=_clamp(a.confidence), origin="ai"))
    return Blueprint.model_validate(bp.model_dump()), warnings
