"""Deterministic blueprint generation (baseline and AI-unavailable fallback).

Everything here is derived from the profile, the column types and a small bilingual
(Lao/English) header glossary. Relationships are proposed only when value overlap is
measured in the data.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.blueprint.context import DatasetContext
from app.blueprint.schema import (
    Aggregation,
    Assumption,
    Blueprint,
    ChartKind,
    ColumnPlan,
    DatasetPlan,
    Domain,
    Filter,
    GeneratorInfo,
    Kpi,
    OpExcludeColumn,
    OpMapValues,
    OpRemoveDuplicates,
    Operation,
    Panel,
    Relationship,
    SemanticRole,
    ValueMapping,
)

GLOSSARY: list[tuple[SemanticRole, re.Pattern]] = [
    (SemanticRole.person, re.compile(r"(agent|staff|employee|operator|officer|manager|supervisor|customer|client|caller|name|ພະນັກງານ|ລູກຄ້າ|ຊື່|ຜູ້|ຫົວໜ້າ)", re.I)),
    (SemanticRole.organization_unit, re.compile(r"(branch|department|dept|team|unit|office|division|ສາຂາ|ພະແນກ|ໜ່ວຍ|ຫ້ອງການ|ກົມ)", re.I)),
    (SemanticRole.location, re.compile(r"(province|city|district|village|region|location|address|ແຂວງ|ເມືອງ|ບ້ານ|ທີ່ຢູ່)", re.I)),
    (SemanticRole.status, re.compile(r"(status|result|outcome|state|ສະຖານະ|ຜົນ)", re.I)),
    (SemanticRole.category, re.compile(r"(type|category|channel|product|service|reason|topic|ປະເພດ|ປະເດັນ|ຊ່ອງທາງ|ຜະລິດຕະພັນ|ບໍລິການ)", re.I)),
]
SUCCESS_WORDS = re.compile(r"(success|completed|complete|answered|resolved|done|ok|ສຳເລັດ|ແກ້ໄຂແລ້ວ)", re.I)
FAIL_WORDS = re.compile(r"(ບໍ່|fail|unsuccess|missed|not )", re.I)


def leaf_label(label: str) -> str:
    """'ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch' → 'ສາຂາ Branch' (drop group header from merged headers)."""
    parts = [p.strip() for p in label.split(" / ") if p.strip()]
    return parts[-1] if parts else label


def classify(col: dict[str, Any], prof: dict[str, Any]) -> tuple[SemanticRole, float, str]:
    t, unit, hint = col["logical_type"], col.get("unit"), col.get("semantic_hint")
    label = f"{col['label']} {col.get('original_label') or ''}"
    if t == "datetime":
        return SemanticRole.timestamp, 0.95, "Values are dates with times."
    if t == "date":
        return SemanticRole.date, 0.95, "Values are calendar dates."
    if t == "duration":
        return SemanticRole.duration, 0.95, "Values are durations (stored in seconds)."
    if t == "phone":
        return SemanticRole.phone, 0.9, "Values look like phone numbers."
    if t == "boolean":
        return SemanticRole.boolean_flag, 0.9, "Values are yes/no."
    if hint == "time_of_day":
        return SemanticRole.time_of_day, 0.9, "Clock times."
    if t in ("integer", "number"):
        if unit == "LAK" or unit in ("USD", "THB"):
            return SemanticRole.currency_amount, 0.9, f"Amounts in {unit}."
        if unit == "percent":
            return SemanticRole.percentage, 0.9, "Percent values."
        if hint == "identifier":
            return SemanticRole.identifier, 0.8, "Integer sequence/code column (header suggests an ID)."
        return SemanticRole.quantity, 0.6, "Numeric values without a unit."
    if t == "text":
        # the leaf header ("ສາຂາ Branch") is more specific than a merged group header ("ຂໍ້ມູນລູກຄ້າ")
        for text, conf in ((leaf_label(col["label"]), 0.75), (label, 0.6)):
            for role, rx in GLOSSARY:
                if rx.search(text):
                    return role, conf, f"Header matches {role.value.replace('_', ' ')} keywords."
        if prof.get("unique_ratio", 0) >= 0.98 and prof.get("max_length", 99) <= 24 and prof.get("non_null", 0) > 5:
            return SemanticRole.identifier, 0.6, "Short values, all unique."
        if hint == "category":
            return SemanticRole.category, 0.6, "Few distinct values repeated across rows."
        return SemanticRole.free_text, 0.5, "Free text."
    return SemanticRole.other, 0.3, "Column has no values."


def _domain(contexts: list[DatasetContext]) -> Domain:
    types = Counter(c["logical_type"] for ctx in contexts for c in ctx.columns)
    units = {c.get("unit") for ctx in contexts for c in ctx.columns}
    if types["duration"] and (types["phone"] or types["datetime"] or types["date"]):
        return Domain(label="Call center / call log", confidence=0.75, origin="rules",
                      rationale="Durations together with phone numbers and dates indicate call records.")
    if "LAK" in units and (types["date"] or types["datetime"]):
        return Domain(label="Financial transactions", confidence=0.55, origin="rules",
                      rationale="Kip amounts recorded against dates.")
    return Domain(label="General tabular data", confidence=0.4, origin="rules",
                  rationale="No strong domain signal in column types.")


def dataset_plan(ctx: DatasetContext, start_id: int = 0) -> DatasetPlan:
    cols: list[ColumnPlan] = []
    used: set[str] = set()
    for c in ctx.columns:
        role, conf, why = classify(c, ctx.prof(c["key"]))
        name = leaf_label(c["label"])[:80]
        base, i = name, 2
        while name.casefold() in used:
            name = f"{base} ({i})"
            i += 1
        used.add(name.casefold())
        cols.append(ColumnPlan(
            key=c["key"], original_label=c["label"], display_name=name, semantic_role=role, confidence=conf,
            rationale=why + (" Shortened from a two-level header." if name != c["label"] else ""),
            origin="rules", status="approved" if name == c["label"] else "proposed",
        ))
    ops: list[Operation] = []
    n = start_id
    for c in ctx.columns:
        p = ctx.prof(c["key"])
        if p.get("spelling_variants"):
            vc = ctx.df[c["key"]].dropna().astype(str).value_counts()
            mappings = []
            for group in p["spelling_variants"]:
                present = [g for g in group if g in vc.index]
                if len(present) < 2:
                    continue
                to = max(present, key=lambda g: (vc[g], g))
                variants = [g for g in present if g != to]
                mappings.append(ValueMapping(to=to, variants=variants, rows_affected=int(sum(vc[v] for v in variants))))
            if mappings:
                n += 1
                desc = "; ".join(f"{' / '.join(m.variants)} → {m.to}" for m in mappings[:3])
                ops.append(Operation(
                    id=f"op{n}", spec=OpMapValues(column=c["key"], mappings=mappings), confidence=0.8, origin="rules",
                    title=f"Unify spelling in {leaf_label(c['label'])}",
                    rationale=f"Values differ only by case, spacing or punctuation: {desc}. The most frequent spelling is kept.",
                ))
        if c["logical_type"] == "empty":
            n += 1
            ops.append(Operation(id=f"op{n}", spec=OpExcludeColumn(column=c["key"]), confidence=0.9, origin="rules",
                                 title=f"Drop empty column {c['label']}", rationale="The column has a header but no values."))
    dups = ctx.profile.get("duplicate_rows", 0)
    if dups:
        n += 1
        ops.append(Operation(
            id=f"op{n}", spec=OpRemoveDuplicates(rows_affected=dups), confidence=0.55, origin="rules",
            title=f"Remove {dups} exact duplicate row(s)",
            rationale="Rows identical in every column. They may be data-entry repeats — or genuine repeated events; review before approving. Removed rows stay listed in lineage.",
        ))
    return DatasetPlan(dataset_id=ctx.dataset_id, version=ctx.version, title=ctx.name[:200],
                       summary=f"Version {ctx.version}: {ctx.profile.get('row_count', 0):,} records × {len(ctx.columns)} columns.",
                       columns=cols, operations=ops, kpis=_kpis(ctx, cols))


def _kpis(ctx: DatasetContext, cols: list[ColumnPlan]) -> list[Kpi]:
    by_role: dict[SemanticRole, list[ColumnPlan]] = {}
    for c in cols:
        by_role.setdefault(c.semantic_role, []).append(c)
    ks: list[Kpi] = [Kpi(id=f"k{ctx.dataset_id}-1", name="Records", description="Number of records.",
                         aggregation=Aggregation.count_rows, confidence=0.95, origin="rules", status="approved")]

    def add(**kw):
        ks.append(Kpi(id=f"k{ctx.dataset_id}-{len(ks) + 1}", origin="rules", **kw))

    for c in by_role.get(SemanticRole.currency_amount, [])[:2]:
        add(name=f"Total {c.display_name}", aggregation=Aggregation.sum, column=c.key, confidence=0.85)
        add(name=f"Average {c.display_name}", aggregation=Aggregation.average, column=c.key, confidence=0.7)
    for c in by_role.get(SemanticRole.duration, [])[:1]:
        add(name=f"Total {c.display_name}", aggregation=Aggregation.sum, column=c.key, confidence=0.85)
        add(name=f"Average {c.display_name}", aggregation=Aggregation.average, column=c.key, confidence=0.8)
    for c in (by_role.get(SemanticRole.person, []) + by_role.get(SemanticRole.organization_unit, []))[:2]:
        add(name=f"Distinct {c.display_name}", aggregation=Aggregation.count_distinct, column=c.key, confidence=0.7)
    for c in by_role.get(SemanticRole.status, [])[:1]:
        vals = ctx.df[c.key].dropna().astype(str).value_counts()
        success = [v for v in vals.index if SUCCESS_WORDS.search(v) and not FAIL_WORDS.search(v)]
        target = success[:1] or list(vals.index[:1])
        if target:
            add(name=f"Share {c.display_name} = {target[0]}", aggregation=Aggregation.share_of_rows,
                filter=Filter(column=c.key, values=target), confidence=0.65 if success else 0.45,
                rationale="Success-like status value." if success else "Most frequent status value.")
    return ks[:10]


def _relationships(contexts: list[DatasetContext]) -> list[Relationship]:
    rels: list[Relationship] = []
    for a in contexts:
        for b in contexts:
            if a.dataset_id == b.dataset_id:
                continue
            for ca in a.columns:
                if ca["logical_type"] != "text":
                    continue
                va = a.distinct_values(ca["key"])
                if len(va) < 2:
                    continue
                for cb in b.columns:
                    if cb["logical_type"] != "text":
                        continue
                    pb = b.prof(cb["key"])
                    if pb.get("distinct", 0) != pb.get("non_null", -1):
                        continue  # 'to' side must be a unique key (lookup table)
                    vb = b.distinct_values(cb["key"])
                    ratio = len(va & vb) / len(va)
                    if ratio >= 0.8:
                        rels.append(Relationship(
                            id=f"r{len(rels) + 1}", from_dataset=a.dataset_id, from_column=ca["key"], to_dataset=b.dataset_id,
                            to_column=cb["key"], match_ratio=round(ratio, 4), verified=True, origin="rules",
                            confidence=0.9 if ratio == 1 else 0.7,
                            rationale=f"{round(ratio * 100)}% of the {len(va)} distinct values of '{ca['label']}' appear in '{cb['label']}', which is unique per row.",
                        ))
    return rels


def _dashboard(plans: list[DatasetPlan]) -> list[Panel]:
    panels: list[Panel] = []
    for p in plans[:1]:
        ks = [k for k in p.kpis if k.status != "rejected"]
        panels.append(Panel(id="p1", title="Key figures", chart=ChartKind.kpi_card, dataset_id=p.dataset_id,
                            kpi_ids=[k.id for k in ks[:6]], origin="rules"))
        roles = {c.semantic_role: c for c in reversed(p.columns)}
        t = roles.get(SemanticRole.timestamp) or roles.get(SemanticRole.date)
        if t:
            panels.append(Panel(id="p2", title="Records over time", chart=ChartKind.line, dataset_id=p.dataset_id,
                                kpi_ids=[ks[0].id], time_column=t.key, origin="rules"))
        dim = roles.get(SemanticRole.organization_unit) or roles.get(SemanticRole.category)
        measure = next((k for k in ks if k.aggregation in (Aggregation.sum,)), ks[0])
        if dim:
            panels.append(Panel(id="p3", title=f"{measure.name} by {dim.display_name}", chart=ChartKind.bar,
                                dataset_id=p.dataset_id, kpi_ids=[measure.id], dimension=dim.key, origin="rules"))
        st = roles.get(SemanticRole.status)
        if st:
            panels.append(Panel(id="p4", title=f"{st.display_name} mix", chart=ChartKind.donut, dataset_id=p.dataset_id,
                                kpi_ids=[ks[0].id], dimension=st.key, origin="rules"))
    return panels


def rules_blueprint(source_id: int, contexts: list[DatasetContext]) -> Blueprint:
    plans = []
    op_counter = 0
    for ctx in contexts:
        p = dataset_plan(ctx, op_counter)
        op_counter += len(p.operations)
        plans.append(p)
    assumptions = []
    for ctx in contexts:
        for c in ctx.columns:
            for note in c.get("notes", []):
                if re.search(r"assumed|interpreted|used (day|month)", note, re.I):
                    assumptions.append(Assumption(id=f"a{len(assumptions) + 1}", text=f"{c['label']}: {note}",
                                                  confidence=0.7, origin="rules", status="approved"))
    return Blueprint(
        source_id=source_id, domain=_domain(contexts), datasets=plans, relationships=_relationships(contexts),
        dashboard=_dashboard(plans), assumptions=assumptions, generator=GeneratorInfo(engine="rules"),
    )
