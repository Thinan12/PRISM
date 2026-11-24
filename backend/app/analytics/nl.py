"""Natural-language questions → QuerySpec (never SQL, never numbers).

Two interpreters produce the same declarative QuerySpec:
* ``ai_parse`` — the configured model maps the question onto the schema; the spec is validated
  against the data and errors are fed back for a bounded number of retries;
* ``rules_parse`` — deterministic bilingual (Lao / English) keyword parser, used when AI is off,
  unavailable, or explicitly requested.
Either way PRISM executes the spec and *writes the answer sentence itself* from the result rows
(``compose_answer``), so every number in an answer comes from the executed SQL.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.analytics.engine import NUMERIC, TEMPORAL, QueryInvalid, QueryResult, validate
from app.analytics.fmt import fmt_change, fmt_period, fmt_value
from app.analytics.spec import Analysis, Op, QuerySpec, TimeGrain
from app.blueprint import ai as ai_mod
from app.blueprint.context import DatasetContext
from app.blueprint.rules import GLOSSARY, leaf_label
from app.blueprint.schema import Aggregation, ChartKind, SemanticRole
from app.core.config import get_settings
from app.ingestion.parsers import LAO_MONTHS, normalize_text

MAX_QUESTION = 500


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _role(col: dict[str, Any]) -> SemanticRole | None:
    t, unit = col["logical_type"], col.get("unit")
    if t in TEMPORAL:
        return SemanticRole.date
    if t == "duration":
        return SemanticRole.duration
    if t == "phone":
        return SemanticRole.phone
    if unit == "LAK":
        return SemanticRole.currency_amount
    if t == "text":
        leaf = leaf_label(col["label"])
        for text in (leaf, col["label"]):
            for role, rx in GLOSSARY:
                if rx.search(text):
                    return role
        return SemanticRole.category if col.get("semantic_hint") == "category" else None
    return None


def _is_identifier(col: dict[str, Any]) -> bool:
    return col.get("semantic_hint") == "identifier" or bool(re.search(r"(^|\W)(id|no\.?|#|ລ/ດ|ລຳດັບ)(\W|$)", col["label"], re.I))


def choose_chart(spec: QuerySpec, ctx: DatasetContext) -> ChartKind:
    if not spec.dimensions:
        return ChartKind.kpi_card if len(spec.measures) == 1 else ChartKind.table
    if len(spec.dimensions) > 1:
        return ChartKind.table
    if (ctx.col(spec.dimensions[0].column) or {}).get("logical_type") in TEMPORAL:
        return ChartKind.line
    if spec.analysis == Analysis.share:
        return ChartKind.donut
    return ChartKind.bar


@dataclass
class Parsed:
    spec: QuerySpec | None
    chart: ChartKind = ChartKind.table
    notes: list[str] = field(default_factory=list)  # how words were mapped (shown to the user)
    reason: str | None = None  # why the question could not be answered
    engine: str = "rules"
    model: str | None = None
    attempts: int | None = None
    fallback_reason: str | None = None
    hints: dict[str, Any] = field(default_factory=dict)  # values / groupings / time grain the question names


# ---------------------------------------------------------------------------
# AI interpreter
# ---------------------------------------------------------------------------


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AIMeasure(_M):
    aggregation: Aggregation
    column: str | None
    filter_column: str | None
    filter_values: list[str]


class AIDimension(_M):
    column: str
    grain: TimeGrain | None


class AICondition(_M):
    column: str
    op: Op
    values: list[str]


class AIQuestion(_M):
    answerable: bool
    reason: str
    measures: list[AIMeasure]
    dimensions: list[AIDimension]
    filters: list[AICondition]
    sort_direction: Literal["asc", "desc"] | None
    limit: int | None
    analysis: Analysis
    chart: ChartKind


QUESTION_SYSTEM = """You translate a user's question about ONE tabular dataset into a structured query. \
You never compute or state numbers: PRISM runs the query and writes the answer.

Use only the column keys given. Rules:
- measures: count_rows (no column) counts records; count_distinct counts different values of a column; \
sum / average / minimum / maximum need a numeric, duration (seconds) or LAK column (minimum/maximum also work on dates); \
share_of_rows is the share of records matching filter_column = filter_values.
- dimensions: group by up to 2 columns; for dates give a grain (day, week, month, quarter, year).
- filters: op is one of eq, neq, in, not_in, gt, gte, lt, lte, between, contains, is_null, not_null. \
Text values must be copied EXACTLY from the listed values (map English words to the Lao values when they mean the same). \
Dates are YYYY-MM-DD; durations are seconds; amounts are plain numbers.
- rankings ("top 5", "which branch has the most"): one dimension, sort_direction desc (asc for lowest), limit.
- analysis: change for trends over time (first dimension must be a date with a grain), share for proportions, \
anomaly for unusual periods (one date dimension), otherwise none.
- chart: kpi_card for a single number, bar for categories, line for time, donut for shares, table otherwise.
- If the data cannot answer the question, set answerable=false and explain why in reason (one sentence)."""


def question_payload(ctx: DatasetContext, question: str, send_values: bool | None = None) -> dict[str, Any]:
    if send_values is None:
        send_values = get_settings().ai_send_values
    cols = []
    for c in ctx.columns:
        p = ctx.prof(c["key"])
        e: dict[str, Any] = {"key": c["key"], "label": c["label"], "type": c["logical_type"], "unit": c.get("unit")}
        if c["logical_type"] in NUMERIC | TEMPORAL:
            e["min"], e["max"] = p.get("min"), p.get("max")
        if send_values and c["logical_type"] == "text" and (p.get("distinct") or 0) <= 200:
            vals = ctx.df[c["key"]].dropna().astype(str).value_counts().head(40).index.tolist()
            e["values"] = [v[:60] for v in vals]
        cols.append(e)
    return {"question": question[:MAX_QUESTION], "dataset": {"name": ctx.name, "rows": len(ctx.df), "columns": cols},
            "values_included": send_values}


def _to_spec(a: AIQuestion, ctx: DatasetContext) -> QuerySpec:
    measures = []
    for m in a.measures[:4]:
        flt = None
        if m.filter_column and m.filter_values:
            flt = {"column": m.filter_column, "op": "in" if len(m.filter_values) > 1 else "eq", "values": m.filter_values}
        measures.append({"aggregation": m.aggregation, "column": m.column, "filter": flt})
    if not measures:
        measures = [{"aggregation": Aggregation.count_rows}]
    spec: dict[str, Any] = {
        "dataset_id": ctx.dataset_id, "measures": measures,
        "dimensions": [{"column": d.column, "grain": d.grain} for d in a.dimensions[:2]],
        "filters": [{"column": f.column, "op": f.op, "values": f.values} for f in a.filters[:12]],
        "analysis": a.analysis,
    }
    time_first = bool(spec["dimensions"]) and (ctx.col(spec["dimensions"][0]["column"]) or {}).get("logical_type") in TEMPORAL
    if a.sort_direction and spec["dimensions"] and not time_first:  # time series always read chronologically
        spec["sort"] = {"by": "measure", "index": 0, "direction": a.sort_direction}
    if a.limit:
        spec["limit"] = max(1, min(1000, a.limit))
    return validate(QuerySpec.model_validate(spec), ctx)


def grounding_problems(spec: QuerySpec, hints: dict[str, Any], ctx: DatasetContext) -> list[str]:
    """Deterministic cross-check of an AI query against what the question demonstrably names:
    values found in the data, explicit groupings ("by / each / which X") and time grains."""
    problems = []
    filtered: dict[str, set[str]] = {}
    for c in [*spec.filters, *(m.filter for m in spec.measures if m.filter is not None)]:
        filtered.setdefault(c.column, set()).update(str(v) for v in c.values)
    grouped = {d.column for d in spec.dimensions}
    for col, vals in (hints.get("values") or {}).items():
        missing = [v for v in vals if v not in filtered.get(col, set())]
        if missing and col not in grouped:
            label = leaf_label((ctx.col(col) or {"label": col})["label"])
            problems.append(f"the question refers to {', '.join(missing)} (values of column '{col}' = {label}) "
                            f"but the query does not filter on them")
    for col in hints.get("groups") or []:
        if col not in grouped:
            label = leaf_label((ctx.col(col) or {"label": col})["label"])
            problems.append(f"the question asks for a breakdown by '{col}' ({label}) but the query does not group by it")
    if hints.get("grain") and not any(d.grain for d in spec.dimensions):
        problems.append("the question asks about periods (day/week/month/…) but the query has no date grouping with a grain")
    by_grain = {d.column for d in spec.dimensions if d.grain}
    for c in [*spec.filters, *(m.filter for m in spec.measures if m.filter is not None)]:
        if c.column in by_grain and c.op in (Op.eq, Op.in_, Op.neq, Op.not_in):
            problems.append(f"the query keeps only exact dates of '{c.column}' while grouping it by period; "
                            "remove that filter (or use 'between' for a date range)")
    return problems


def ai_parse(question: str, ctx: DatasetContext, hints: dict[str, Any] | None = None) -> Parsed:
    """Raises ai_mod.AIUnavailable when no valid query is obtained."""
    holder: dict[str, QuerySpec] = {}

    def check(a: AIQuestion) -> None:
        if not a.answerable:
            return
        try:
            holder["spec"] = _to_spec(a, ctx)
        except (QueryInvalid, ValueError) as e:
            raise ValueError(f"the query does not fit the data: {e}") from e
        problems = grounding_problems(holder["spec"], hints or {}, ctx)
        if problems:
            raise ValueError("the query does not answer the question: " + "; ".join(problems))

    payload = question_payload(ctx, question)
    res = ai_mod.structured_call(
        QUESTION_SYSTEM, "Question and dataset schema:\n" + json.dumps(payload, ensure_ascii=False, default=str),
        AIQuestion, array_caps={"measures": 4, "dimensions": 2, "filters": 12, "filter_values": 20, "values": 20},
        string_caps={"reason": 300, "column": 80, "filter_column": 80}, check=check, name="prism_question")
    a = res.value
    if not a.answerable:
        return Parsed(spec=None, reason=a.reason[:300] or "The data cannot answer this question.", engine="ai",
                      model=res.model, attempts=res.attempts)
    spec = holder["spec"]
    return Parsed(spec=spec, chart=a.chart if a.chart in ChartKind else choose_chart(spec, ctx), engine="ai",
                  model=res.model, attempts=res.attempts)


# ---------------------------------------------------------------------------
# deterministic bilingual parser
# ---------------------------------------------------------------------------

ROLE_WORDS: dict[SemanticRole, list[str]] = {
    SemanticRole.currency_amount: ["cost", "costs", "price", "amount", "revenue", "money", "charge", "fee", "spend",
                                   "kip", "lak", "ຄ່າ", "ເງິນ", "ລາຄາ", "ກີບ"],
    SemanticRole.duration: ["duration", "talk time", "call length", "length", "minutes", "seconds", "how long",
                            "ໄລຍະເວລາ", "ເວລາໂທ", "ນາທີ", "ວິນາທີ"],
    SemanticRole.organization_unit: ["branch", "branches", "department", "team", "office", "ສາຂາ", "ພະແນກ", "ຫ້ອງການ"],
    SemanticRole.person: ["agent", "agents", "staff", "employee", "employees", "operator", "who", "ພະນັກງານ", "ໃຜ"],
    SemanticRole.status: ["status", "result", "outcome", "ສະຖານະ", "ຜົນ"],
    SemanticRole.category: ["type", "types", "category", "categories", "kind", "reason", "ປະເພດ"],
    SemanticRole.location: ["province", "city", "district", "location", "ແຂວງ", "ເມືອງ"],
    SemanticRole.phone: ["phone", "phone number", "number called", "ເບີ"],
    SemanticRole.date: ["date", "day", "when", "ວັນທີ"],
}
VALUE_ALIASES: dict[str, list[str]] = {  # common call-centre words → Lao values
    "ຮ້ອງຮຽນ": ["complaint", "complaints"], "ສອບຖາມ": ["inquiry", "inquiries", "enquiry", "enquiries", "question", "questions"],
    "ຂາຍ": ["sale", "sales", "selling"], "ສຳເລັດ": ["successful", "success", "completed", "resolved"],
    "ບໍ່ສຳເລັດ": ["unsuccessful", "failed", "failure", "not successful"], "ໂທກັບ": ["callback", "call back", "callbacks"],
    "ວຽງຈັນ": ["vientiane"], "ປາກເຊ": ["pakse"], "ຫຼວງພະບາງ": ["luang prabang", "luangprabang"],
    "ສະຫວັນນະເຂດ": ["savannakhet"],
}
W = {
    "count": ["how many", "number of", "count", "ຈຳນວນ", "ເທົ່າໃດ", "ຈັກ"],
    "sum": ["total", "sum", "overall", "altogether", "ລວມ", "ທັງໝົດ"],
    "avg": ["average", "avg", "mean", "typical", "ສະເລ່ຍ"],
    "max": ["maximum", "max", "highest", "longest", "largest", "biggest", "most expensive", "ສູງສຸດ", "ຍາວທີ່ສຸດ", "ຫຼາຍທີ່ສຸດ"],
    "min": ["minimum", "min", "lowest", "shortest", "smallest", "cheapest", "ຕ່ຳສຸດ", "ສັ້ນທີ່ສຸດ", "ໜ້ອຍທີ່ສຸດ"],
    "top": ["top", "most", "best", "highest", "largest", "biggest", "ສູງສຸດ", "ຫຼາຍທີ່ສຸດ", "ຫຼາຍສຸດ", "ອັນດັບ"],
    "bottom": ["bottom", "least", "fewest", "lowest", "worst", "smallest", "ໜ້ອຍທີ່ສຸດ", "ໜ້ອຍສຸດ", "ຕ່ຳສຸດ"],
    "by": [" by ", " per ", " each ", "for every", "breakdown", "broken down", "split by", "ແຕ່ລະ", "ຕາມ", "ແຍກຕາມ"],
    "which": ["which", "what branch", "what agent", "who", "ໃດ", "ໃຜ"],
    "trend": ["trend", "over time", "growth", "grow", "increase", "decrease", "change", "ແນວໂນ້ມ", "ປ່ຽນແປງ", "ເພີ່ມຂຶ້ນ", "ຫຼຸດລົງ"],
    "share": ["share", "percent", "percentage", "proportion", "ratio", "%", "ສັດສ່ວນ", "ເປີເຊັນ"],
    "anomaly": ["unusual", "anomal", "spike", "outlier", "abnormal", "strange", "ຜິດປົກກະຕິ", "ແປກ"],
    "latest": ["latest", "most recent", "last call", "ລ່າສຸດ"],
    "earliest": ["earliest", "first call", "ທຳອິດ"],
    "rows": ["calls", "call", "records", "rows", "entries", "ການໂທ", "ສາຍ"],
}
GRAIN_WORDS: list[tuple[TimeGrain, list[str]]] = [
    (TimeGrain.day, ["daily", "per day", "by day", "each day", "every day", "day by day", "ລາຍວັນ", "ແຕ່ລະມື້", "ລາຍມື້"]),
    (TimeGrain.week, ["weekly", "per week", "by week", "each week", "ລາຍອາທິດ", "ແຕ່ລະອາທິດ"]),
    (TimeGrain.month, ["monthly", "per month", "by month", "each month", "month by month", "ລາຍເດືອນ", "ແຕ່ລະເດືອນ"]),
    (TimeGrain.quarter, ["quarterly", "per quarter", "by quarter", "each quarter", "ລາຍໄຕມາດ", "ແຕ່ລະໄຕມາດ"]),
    (TimeGrain.year, ["yearly", "per year", "by year", "each year", "annual", "ລາຍປີ", "ແຕ່ລະປີ"]),
]
EN_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
NUM_WORD = r"(\d+(?:[.,]\d+)?)"
CMP_WORDS = [("gt", ["more than", "longer than", "over", "above", "greater than", "exceeding", ">", "ຫຼາຍກວ່າ", "ເກີນ", "ຍາວກວ່າ"]),
             ("lt", ["less than", "shorter than", "under", "below", "fewer than", "<", "ໜ້ອຍກວ່າ", "ຕ່ຳກວ່າ", "ສັ້ນກວ່າ"])]
UNIT_SECONDS = [(r"(hours?|hrs?|h|ຊົ່ວໂມງ)", 3600), (r"(minutes?|mins?|m|ນາທີ)", 60), (r"(seconds?|secs?|s|ວິນາທີ)", 1)]


def _find(term: str, text: str) -> tuple[int, int] | None:
    """Span of ``term`` in ``text``: whole-word for Latin terms, substring for Lao (no word spaces)."""
    if re.match(r"^[a-z ]+$", term):
        m = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text)
        return m.span() if m else None
    i = text.find(term)
    return (i, i + len(term)) if i >= 0 else None


def _has(text: str, words: list[str]) -> str | None:
    for w in words:
        if re.match(r"^[a-z ]+$", w.strip()) and w.strip():
            if re.search(rf"(?<![a-z]){re.escape(w.strip())}(?![a-z])", text):
                return w.strip()
        elif w in text:
            return w
    return None


def _terms(col: dict[str, Any]) -> list[str]:
    """Words that refer to a column: its header pieces plus role synonyms."""
    out: set[str] = set()
    generic = {w.strip() for w in W["rows"]}
    # leaf header only: group headers of merged cells ("ການໂທ / …") are shared by several columns
    for piece in re.split(r"[\s/()\[\]:,_\-]+", normalize_text(leaf_label(col["label"])).casefold()):
        if piece not in generic and (len(piece) >= 3 or (re.search(r"[຀-໿]", piece) and len(piece) >= 2)):
            out.add(piece)
    role = _role(col)
    if role in ROLE_WORDS:
        out.update(ROLE_WORDS[role])
    return sorted(out, key=len, reverse=True)


def _year_of(month: int, ctx: DatasetContext, date_key: str | None) -> int | None:
    if date_key is None:
        return None
    s = ctx.df[date_key].dropna()
    years = sorted({ts.year for ts in s if ts.month == month}, reverse=True)
    return years[0] if years else (int(s.max().year) if len(s) else None)


def _date_filter(text: str, ctx: DatasetContext, date_key: str | None, notes: list[str]) -> dict[str, Any] | None:
    if date_key is None:
        return None
    year = None
    ym = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if ym:
        year = int(ym.group(1))
        if year > 2400:  # Buddhist Era
            year -= 543
    iso = re.search(r"(?<!\d)(\d{4})-(\d{2})(?!\d)", text)
    month = None
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
    else:
        for name, num in sorted(LAO_MONTHS.items(), key=lambda kv: -len(kv[0])):
            if name in text:
                month = num
                break
        if month is None:
            for name, num in EN_MONTHS.items():
                if re.search(rf"(?<![a-z]){name}(?![a-z])", text) and not (name == "may" and re.search(r"\bmay\s+(i|we|you)\b", text)):
                    month = num
                    break
    qm = re.search(r"(?:\bq|quarter\s*|ໄຕມາດ\s*)([1-4])\b", text)
    if month is not None:
        y = year or _year_of(month, ctx, date_key)
        if y is None:
            return None
        last = calendar.monthrange(y, month)[1]
        notes.append(f"Period: {calendar.month_name[month]} {y}.")
        return {"column": date_key, "op": "between", "values": [f"{y}-{month:02d}-01", f"{y}-{month:02d}-{last:02d}"]}
    if qm:
        qn = int(qm.group(1))
        s = ctx.df[date_key].dropna()
        y = year or (int(s.max().year) if len(s) else None)
        if y is None:
            return None
        m0 = 3 * (qn - 1) + 1
        notes.append(f"Period: Q{qn} {y}.")
        return {"column": date_key, "op": "between",
                "values": [f"{y}-{m0:02d}-01", f"{y}-{m0 + 2:02d}-{calendar.monthrange(y, m0 + 2)[1]:02d}"]}
    if ym and re.search(rf"(?:\bin|\bduring|\bfor|\bof|ປີ)\s*{ym.group(1)}", text):
        notes.append(f"Period: year {year}.")
        return {"column": date_key, "op": "between", "values": [f"{year}-01-01", f"{year}-12-31"]}
    return None


def _threshold(text: str, ctx: DatasetContext, numeric: list[dict[str, Any]], notes: list[str]) -> dict[str, Any] | None:
    for op, words in CMP_WORDS:
        for w in words:
            m = re.search(re.escape(w) + r"\s*" + NUM_WORD + r"\s*([^\s\d?.!,;:]*)", text)
            if not m:
                continue
            value = float(m.group(1).replace(",", ""))
            unit = m.group(2)
            target = None
            for rx, mult in UNIT_SECONDS:
                if unit and re.fullmatch(rx, unit):
                    target = next((c for c in numeric if c["logical_type"] == "duration"), None)
                    value *= mult
                    break
            if target is None and (unit in ("kip", "lak", "₭", "ກີບ") or _has(text, ROLE_WORDS[SemanticRole.currency_amount])):
                target = next((c for c in numeric if c.get("unit") == "LAK"), None)
            if target is None and _has(text, ROLE_WORDS[SemanticRole.duration]):
                target = next((c for c in numeric if c["logical_type"] == "duration"), None)
                if target is not None and not unit:
                    value *= 60  # "longer than 5" about durations means minutes
            if target is None:
                continue
            notes.append(f"Condition: {leaf_label(target['label'])} {'>' if op == 'gt' else '<'} {value:g}"
                         + (" seconds" if target["logical_type"] == "duration" else "") + ".")
            return {"column": target["key"], "op": op, "values": [value]}
    return None


def rules_parse(question: str, ctx: DatasetContext) -> Parsed:
    text = " " + normalize_text(question).casefold().strip() + " "
    notes: list[str] = []
    cols = [c for c in ctx.columns if c["logical_type"] != "empty"]
    date_cols = [c for c in cols if c["logical_type"] in TEMPORAL]
    numeric = [c for c in cols if c["logical_type"] in NUMERIC and not _is_identifier(c)]
    date_key = date_cols[0]["key"] if date_cols else None

    # 1. values mentioned (exact data values, or English aliases of Lao values)
    value_hits: dict[str, list[str]] = {}
    used_spans: list[tuple[int, int]] = []
    for c in cols:
        if c["logical_type"] != "text" or (ctx.prof(c["key"]).get("distinct") or 0) > 500:
            continue
        for v in sorted(ctx.distinct_values(c["key"]), key=len, reverse=True):
            vn = normalize_text(v).casefold()
            if len(vn) < 2:
                continue
            hit = None
            if re.search(r"[຀-໿]", vn):
                i = text.find(vn)
                hit = (i, i + len(vn)) if i >= 0 else None
                if hit is None:
                    for alias in VALUE_ALIASES.get(v, []):
                        m = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text)
                        if m:
                            hit = m.span()
                            notes.append(f"'{alias}' → {v}.")
                            break
            else:
                m = re.search(rf"(?<![\w]){re.escape(vn)}(?![\w])", text)
                hit = m.span() if m else None
            if hit and not any(a < hit[1] and hit[0] < b for a, b in used_spans):
                used_spans.append(hit)
                value_hits.setdefault(c["key"], []).append(v)

    # 2. columns mentioned (outside value spans)
    def mentioned(c: dict[str, Any]) -> int:
        best = 0
        for t in _terms(c):
            span = _find(t, text)
            if span and not any(x < span[1] and span[0] < y for x, y in used_spans):
                best = max(best, len(t))
        return best

    scores = {c["key"]: mentioned(c) for c in cols}
    agg_words = {k: _has(text, W[k]) for k in ("count", "sum", "avg", "max", "min", "top", "bottom", "by", "which", "trend",
                                               "share", "anomaly", "latest", "earliest", "rows")}
    grain = next((g for g, words in GRAIN_WORDS if _has(text, words)), None)
    if grain is None:
        for g, words in ((TimeGrain.month, ["month", "ເດືອນ"]), (TimeGrain.week, ["week", "ອາທິດ"]),
                         (TimeGrain.day, ["day", "days", "ມື້"]), (TimeGrain.quarter, ["quarter"]), (TimeGrain.year, ["year"])):
            if (agg_words["by"] or agg_words["trend"] or agg_words["anomaly"]) and _has(text, words):
                grain = g
                break
    if grain is None and (agg_words["trend"] or agg_words["anomaly"]) and date_key:
        grain = TimeGrain.day if agg_words["anomaly"] else TimeGrain.month

    # 3. filters
    filters: list[dict[str, Any]] = []
    df_ = _date_filter(text, ctx, date_key, notes)
    if df_:
        filters.append(df_)
    th = _threshold(text, ctx, numeric, notes)
    if th:
        filters.append(th)
    for key, vals in value_hits.items():
        filters.append({"column": key, "op": "in" if len(vals) > 1 else "eq", "values": vals})
        notes.append(f"Filter: {leaf_label((ctx.col(key) or {'label': key})['label'])} = {', '.join(vals)}.")

    # 4. dimension
    dims: list[dict[str, Any]] = []
    if grain is not None and date_key:
        dims.append({"column": date_key, "grain": grain})
    cat_candidates = sorted((c for c in cols if c["logical_type"] in ("text", "phone", "boolean") and scores[c["key"]]),
                            key=lambda c: -scores[c["key"]])
    wants_group = bool(agg_words["by"] or agg_words["which"] or agg_words["share"] or agg_words["top"] or agg_words["bottom"])
    compare = bool(re.search(r"\b(compare|vs\.?|versus)\b|ປຽບທຽບ", text))
    if not dims or (cat_candidates and agg_words["by"]):  # e.g. "calls per month by branch" → two groupings
        for c in cat_candidates:
            if c["key"] in value_hits and not compare and len(value_hits[c["key"]]) == 1:
                continue  # "complaints" is a filter, not a grouping
            if wants_group or compare:
                dims.append({"column": c["key"], "grain": None})
                break
    if compare and not dims:
        multi = next((k for k, v in value_hits.items() if len(v) > 1), None)
        if multi:
            dims.append({"column": multi, "grain": None})

    # 5. measure
    num_hit = sorted((c for c in numeric if scores[c["key"]]), key=lambda c: -scores[c["key"]])
    date_hit = next((c for c in date_cols if scores[c["key"]]), None)
    measures: list[dict[str, Any]] = []
    analysis = Analysis.none
    if agg_words["anomaly"] and date_key:
        measures = [{"aggregation": "sum", "column": num_hit[0]["key"]} if num_hit and agg_words["sum"] else {"aggregation": "count_rows"}]
        dims = [{"column": date_key, "grain": grain or TimeGrain.day}]
        analysis = Analysis.anomaly
    elif (agg_words["latest"] or agg_words["earliest"]) and date_key and not dims:
        measures = [{"aggregation": "maximum" if agg_words["latest"] else "minimum", "column": (date_hit or date_cols[0])["key"]}]
    else:
        target = num_hit[0] if num_hit else None
        if agg_words["avg"]:
            target = target or next((c for c in numeric if c.get("unit") == "LAK"), None) or next(iter(numeric), None)
            if target:
                measures.append({"aggregation": "average", "column": target["key"]})
        elif agg_words["sum"] and (target or not agg_words["count"]):
            target = target or next((c for c in numeric if c.get("unit") == "LAK"), None) or next(iter(numeric), None)
            if target:
                measures.append({"aggregation": "sum", "column": target["key"]})
        elif (agg_words["max"] or agg_words["min"]) and target and not dims:
            measures.append({"aggregation": "maximum" if agg_words["max"] else "minimum", "column": target["key"]})
        elif target and dims and (agg_words["top"] or agg_words["bottom"] or agg_words["by"] or agg_words["which"]) and not agg_words["count"]:
            measures.append({"aggregation": "sum", "column": target["key"]})
        if not measures:
            distinct = next((c for c in cat_candidates if c["key"] not in value_hits
                             and all(d["column"] != c["key"] for d in dims)), None)
            if agg_words["count"] and distinct is not None and not agg_words["rows"] and not dims:
                measures.append({"aggregation": "count_distinct", "column": distinct["key"]})
            else:
                measures.append({"aggregation": "count_rows"})
        if agg_words["share"]:
            if dims:
                analysis = Analysis.share
            else:
                f = next((f for f in filters if f["column"] in value_hits), None)
                if f is not None:
                    filters.remove(f)
                    measures = [{"aggregation": "share_of_rows", "filter": f}]
        elif agg_words["trend"] and dims and dims[0].get("grain"):
            analysis = Analysis.change

    # what the question demonstrably asks for — used to check an AI interpretation (see ai_parse)
    hints = {"values": value_hits,
             "groups": [d["column"] for d in dims if not d.get("grain")] if (agg_words["by"] or agg_words["which"]) else [],
             "grain": bool(grain)}
    if not dims and not filters and all(m["aggregation"] == "count_rows" for m in measures) and not any(agg_words.values()):
        return Parsed(spec=None, notes=notes, hints=hints, reason="I could not relate this question to the columns of this dataset. "
                      "Try naming a column or value, e.g. " + _examples(ctx))

    spec: dict[str, Any] = {"dataset_id": ctx.dataset_id, "measures": measures, "dimensions": dims[:2], "filters": filters,
                            "analysis": analysis}
    n = re.search(r"(?:top|bottom|ອັນດັບ)\s*(\d{1,3})", text)
    if dims and not (dims[0].get("grain")) and (agg_words["top"] or agg_words["bottom"] or agg_words["which"] or n):
        spec["sort"] = {"by": "measure", "index": 0, "direction": "asc" if agg_words["bottom"] and not agg_words["top"] else "desc"}
        spec["limit"] = int(n.group(1)) if n else 10
    try:
        q = validate(QuerySpec.model_validate(spec), ctx)
    except (QueryInvalid, ValueError) as e:
        return Parsed(spec=None, notes=notes, hints=hints, reason=f"The question maps to a query that does not fit the data ({e}).")
    return Parsed(spec=q, chart=choose_chart(q, ctx), notes=notes, hints=hints)


def _examples(ctx: DatasetContext) -> str:
    cat = next((c for c in ctx.columns if c["logical_type"] == "text" and _role(c) in
                (SemanticRole.organization_unit, SemanticRole.person, SemanticRole.category)), None)
    num = next((c for c in ctx.columns if c["logical_type"] in NUMERIC and not _is_identifier(c)), None)
    ex = []
    if num and cat:
        ex.append(f"“total {leaf_label(num['label'])} by {leaf_label(cat['label'])}”")
    if any(c["logical_type"] in TEMPORAL for c in ctx.columns):
        ex.append("“records per month”")
    return " or ".join(ex) or "“how many records”"


# ---------------------------------------------------------------------------
# answer text — written by PRISM from the executed result
# ---------------------------------------------------------------------------


def compose_answer(res: QueryResult) -> str:
    cols = res.columns
    rows = [dict(zip([c.key for c in cols], r)) for r in res.rows]
    meas = [c for c in cols if c.kind == "measure"]
    dims = [c for c in cols if c.kind == "dimension"]
    if res.matched_rows == 0:
        return "No records match these conditions."
    if not rows:
        return "The query returned no rows."
    m0 = meas[0]
    if not dims:
        r = rows[0]
        return " · ".join(f"{m.label}: {fmt_value(r[m.key], m.format)}" for m in meas) + \
            (f" (from {res.matched_rows:,} of {res.total_rows:,} records)" if res.matched_rows != res.total_rows else "")
    d0 = dims[0]

    def dv(r: dict[str, Any], d=d0) -> str:
        return fmt_period(r[d.key], d.grain) if d.grain or d.type in TEMPORAL else fmt_value(r[d.key], d.format)

    if any(c.kind == "anomaly" for c in cols):
        flagged = [r for r in rows if r.get("anomaly")]
        unit = d0.grain or "period"
        typical = fmt_value(rows[0].get("typical"), m0.format)
        if not flagged:
            return f"No unusual {unit}s: all {len(rows)} {unit}s are within the normal range (typical {m0.label.lower()}: {typical})."
        top = sorted(flagged, key=lambda r: -abs(r.get("z") or 0))[:5]
        items = ", ".join(f"{dv(r)} ({fmt_value(r['m0'], m0.format)})" for r in top)
        return f"{len(flagged)} unusual {unit}{'s' if len(flagged) > 1 else ''} out of {len(rows)} (typical: {typical}): {items}."
    if d0.type in TEMPORAL and len(dims) == 1:
        rows = sorted(rows, key=lambda r: (r[d0.key] is None, str(r[d0.key])))  # narrate chronologically
        first, last = rows[0], rows[-1]
        peak = max(rows, key=lambda r: (r[m0.key] is not None, r[m0.key] or 0))
        s = (f"{m0.label} went from {fmt_value(first[m0.key], m0.format)} in {dv(first)} to "
             f"{fmt_value(last[m0.key], m0.format)} in {dv(last)}")
        if len(rows) > 1 and "change_pct" in last:
            s += f"; the latest change is {fmt_change(last.get('change_pct'))}"
        return s + f". Highest: {dv(peak)} ({fmt_value(peak[m0.key], m0.format)})."
    if len(dims) == 1:
        if len(rows) == 1:
            r = rows[0]
            return f"{dv(r)}: {fmt_value(r[m0.key], m0.format)}."
        desc = res.spec.sort is None or res.spec.sort.direction == "desc"
        counted = res.spec.measures[0].aggregation in (Aggregation.count_rows, Aggregation.count_distinct)
        word = ("most" if desc else "fewest") if counted else ("highest" if desc else "lowest")
        what = m0.label if not counted or m0.label != "Records" else "records"
        top = rows[0]
        ties = [r for r in rows if r[m0.key] == top[m0.key]]
        lead = (" and ".join(dv(r) for r in ties[:3]) + f" tie for the {word} {what}" if len(ties) > 1
                else f"{dv(top)} has the {word} {what}")
        s = f"{lead}: {fmt_value(top[m0.key], m0.format)}"
        rest = [r for r in rows if r not in ties][:2]
        if rest:
            s += " — followed by " + " and ".join(f"{dv(r)} ({fmt_value(r[m0.key], m0.format)})" for r in rest)
        if "share" in top and top.get("share") is not None:
            s += f". {dv(top)} accounts for {fmt_value(top['share'], 'percent')} of the total"
        more = f" ({len(rows)} groups{', more not shown' if res.truncated else ''})"
        return s + more + "."
    top = rows[0]
    return (f"{len(rows)} combinations of {d0.label} and {dims[1].label}; the first is {dv(top)} / "
            f"{dv(top, dims[1])} with {m0.label.lower()} {fmt_value(top[m0.key], m0.format)}.")


def question_order() -> str:
    """rules_first | ai_first. ``auto``: small local models answer worse than the deterministic parser
    on questions it understands (measured with qwen2.5:7b), so Ollama is consulted only when the rules
    cannot map a question; hosted models (Claude, DeepSeek…) go first."""
    order = (get_settings().question_engine or "auto").lower()
    if order in ("rules_first", "ai_first"):
        return order
    return "rules_first" if ai_mod.provider() == "ollama" else "ai_first"


def interpret_question(question: str, ctx: DatasetContext, engine: str = "auto") -> Parsed:
    """engine: auto (policy above) | rules (never call the model) | ai (model first)."""
    question = question.strip()[:MAX_QUESTION]
    rules = rules_parse(question, ctx)
    if engine == "rules":
        return rules
    if not ai_mod.ai_configured():
        reason = ai_mod.not_configured_reason()
        rules.fallback_reason = f"{reason}; interpreted by PRISM's rule-based parser." if reason else None
        return rules
    if engine == "auto" and question_order() == "rules_first" and rules.spec is not None:
        return rules
    try:
        p = ai_parse(question, ctx, rules.hints)
        if p.spec is None and rules.spec is not None:  # the model gave up but the rules can answer
            rules.fallback_reason = f"The AI could not map the question ({p.reason}); PRISM's rule-based parser answered."
            return rules
        return p
    except ai_mod.AIUnavailable as e:
        rules.fallback_reason = f"{e} Interpreted by PRISM's rule-based parser instead."
        return rules
    except Exception as e:  # noqa: BLE001 - never block the user on an AI-side bug
        rules.fallback_reason = f"AI interpretation failed unexpectedly ({type(e).__name__}); rule-based parser used."
        return rules
