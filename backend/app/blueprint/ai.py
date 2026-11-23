"""Claude-assisted semantic interpretation.

Privacy & correctness rules:
* Only profiles, schema metadata and a few bounded, masked sample values are sent —
  never the dataset rows (``build_payload`` enforces the bounds; tests assert them).
* The model's JSON is constrained by a JSON schema (structured outputs) and then validated
  with Pydantic; validation errors are fed back for a bounded number of retries.
* Everything the model proposes is checked against the real data in ``merge.py`` before it
  can enter a Blueprint. The model never produces numbers that PRISM reports.
* Any failure (no key, network, refusal, invalid output) → the caller falls back to the
  rule-based blueprint and records why.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from app.blueprint.context import DatasetContext
from app.blueprint.schema import Aggregation, Blueprint, ChartKind, SemanticRole
from app.core.config import get_settings
from app.core.logging import log

MAX_SAMPLES = 5
MAX_TOP_VALUES = 8
MAX_VALUE_CHARS = 60
MAX_ISSUES = 15


# ---------------------------------------------------------------------------
# AI output contract (all fields required; optional values are nullable)
# ---------------------------------------------------------------------------


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AIDomain(_M):
    label: str
    confidence: float
    rationale: str


class AIColumn(_M):
    key: str
    display_name: str
    description: str
    semantic_role: SemanticRole
    confidence: float
    rationale: str


class AIKpi(_M):
    name: str
    description: str
    aggregation: Aggregation
    column: str | None
    filter_column: str | None
    filter_values: list[str]
    confidence: float
    rationale: str


class AIValueMapping(_M):
    column: str
    canonical: str
    variants: list[str]
    confidence: float
    rationale: str


class AIExclude(_M):
    column: str
    confidence: float
    rationale: str


class AIDataset(_M):
    dataset_ref: str
    title: str
    summary: str
    columns: list[AIColumn]
    kpis: list[AIKpi]
    value_mappings: list[AIValueMapping]
    exclude_columns: list[AIExclude]


class AIRelationship(_M):
    from_dataset: str
    from_column: str
    to_dataset: str
    to_column: str
    confidence: float
    rationale: str


class AIPanel(_M):
    dataset_ref: str
    title: str
    chart: ChartKind
    kpi_names: list[str]
    dimension_column: str | None
    time_column: str | None


class AIAssumption(_M):
    text: str
    confidence: float


class AIInterpretation(_M):
    domain: AIDomain
    datasets: list[AIDataset]
    relationships: list[AIRelationship]
    dashboard: list[AIPanel]
    assumptions: list[AIAssumption]


_UNSUPPORTED = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
                "maxLength", "minItems", "maxItems", "pattern", "multipleOf", "format"}


def api_schema(model: type[BaseModel] = AIInterpretation) -> dict[str, Any]:
    """Pydantic JSON schema adapted to the structured-outputs subset: no unsupported keywords,
    additionalProperties:false and all properties required on every object."""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k in list(node):
                if k in _UNSUPPORTED and not isinstance(node.get(k), dict):
                    node.pop(k)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


# ---------------------------------------------------------------------------
# Payload (profile only)
# ---------------------------------------------------------------------------

_PHONE_LIKE = re.compile(r"^\+?\d[\d\s\-]{6,}$")


def _short(v: Any) -> Any:
    if isinstance(v, str):
        s = v if len(v) <= MAX_VALUE_CHARS else v[: MAX_VALUE_CHARS - 1] + "…"
        if _PHONE_LIKE.match(s):
            digits = re.sub(r"\D", "", s)
            s = digits[:3] + "*" * max(0, len(digits) - 6) + digits[-3:]
        return s
    return v


def build_payload(contexts: list[DatasetContext], rules_bp: Blueprint, requirements: str | None = None,
                  send_values: bool | None = None) -> dict[str, Any]:
    if send_values is None:
        send_values = get_settings().ai_send_values
    datasets = []
    for ctx in contexts:
        cols = []
        for c in ctx.columns:
            p = ctx.prof(c["key"])
            mask = c["logical_type"] == "phone"
            entry: dict[str, Any] = {
                "key": c["key"], "label": c["label"], "original_label": c.get("original_label"),
                "type": c["logical_type"], "unit": c.get("unit"), "hint": c.get("semantic_hint"),
                "missing_pct": p.get("missing_pct"), "distinct": p.get("distinct"),
                "samples": [("<masked phone>" if mask else _short(s)) for s in (p.get("samples") or [])[:MAX_SAMPLES]] if send_values else [],
            }
            if p.get("top_values") and not mask and send_values:
                entry["top_values"] = [{"value": _short(t["value"]), "count": t["count"]} for t in p["top_values"][:MAX_TOP_VALUES]]
            for k in ("min", "max", "mean", "median"):
                if p.get(k) is not None and c["logical_type"] in ("integer", "number", "duration", "date", "datetime"):
                    entry[k] = p[k]
            if p.get("spelling_variants") and send_values:
                entry["spelling_variant_groups"] = [[_short(v) for v in g[:6]] for g in p["spelling_variants"][:5]]
            cols.append(entry)
        datasets.append({
            "dataset_ref": str(ctx.dataset_id),
            "name": ctx.name,
            "sheet": ctx.lineage.get("sheet"),
            "row_count": ctx.profile.get("row_count"),
            "duplicate_rows": ctx.profile.get("duplicate_rows"),
            "columns": cols,
            "issues": [i["code"] if not send_values else i["message"][:200] for i in ctx.profile.get("issues", [])[:MAX_ISSUES]],
        })
    return {
        "datasets": datasets,
        "measured_relationships": [
            {"from_dataset": str(r.from_dataset), "from_column": r.from_column, "to_dataset": str(r.to_dataset),
             "to_column": r.to_column, "match_ratio": r.match_ratio}
            for r in rules_bp.relationships
        ],
        "user_requirements": (requirements or "")[:2000],
        "values_included": send_values,
        "locale": {"country": "Laos", "languages": ["lo", "en"], "currency": "LAK", "timezone": "Asia/Vientiane"},
    }


SYSTEM = """You are PRISM's data-interpretation engine. You receive PROFILES of tabular datasets (schema, \
statistics, a few masked sample values) — never the full data. Your job is semantic interpretation only.

Produce a JSON object that:
- names the business domain of the data;
- for EVERY column (use the exact `key` given): a concise display name (keep Lao and English where the \
original header mixes them; at most 60 characters; unique within the dataset), a one-sentence description, \
a semantic role, a confidence 0–1 and a short rationale;
- proposes up to 8 meaningful KPIs per dataset using ONLY the allowed aggregations over existing column keys \
(sum/average/minimum/maximum only on numeric or duration columns; share_of_rows needs filter_column and \
filter_values copied exactly from the observed values). Never state KPI values — PRISM computes them;
- proposes value mappings only for real spelling variants of the same thing (variants must be values that \
appear in the profile, copied exactly);
- proposes relationships only between existing columns (measured overlaps are provided);
- plans up to 6 dashboard panels referencing your KPI names and existing column keys;
- lists the assumptions you made, each with a confidence.

Rules: never invent columns, values, or numbers; when unsure, lower the confidence and say why in the rationale; \
write rationales in English; preserve Lao script exactly."""


# ---------------------------------------------------------------------------
# Client call with validation + retries
# ---------------------------------------------------------------------------


class AIUnavailable(Exception):
    pass


@dataclass
class AIResult:
    interpretation: AIInterpretation
    model: str
    attempts: int
    input_tokens: int
    output_tokens: int


ClientFactory = Callable[[], Any]


def _default_client() -> Any:
    import anthropic

    s = get_settings()
    return anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.ai_timeout_seconds, max_retries=2)


client_factory: ClientFactory = _default_client


def ai_configured() -> bool:
    return bool(get_settings().anthropic_api_key)


def interpret(payload: dict[str, Any], max_attempts: int | None = None) -> AIResult:
    """Call Claude and return a schema-valid interpretation, or raise AIUnavailable."""
    import anthropic

    s = get_settings()
    if not s.anthropic_api_key:
        raise AIUnavailable("AI is not configured (ANTHROPIC_API_KEY is not set).")
    attempts = max_attempts or (s.ai_max_retries + 1)
    client = client_factory()
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": "Interpret these dataset profiles and return the JSON interpretation.\n\n"
                   + json.dumps(payload, ensure_ascii=False, default=str),
    }]
    schema = api_schema()
    tokens_in = tokens_out = 0
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        try:
            resp = client.beta.messages.create(
                model=s.ai_model,
                max_tokens=16000,
                system=SYSTEM,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": schema}, "effort": s.ai_effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise AIUnavailable("The Anthropic API key was rejected.") from e
        except anthropic.PermissionDeniedError as e:
            raise AIUnavailable("The Anthropic API key lacks permission for this model.") from e
        except anthropic.NotFoundError as e:
            raise AIUnavailable(f"Model '{s.ai_model}' is not available.") from e
        except anthropic.RateLimitError as e:
            raise AIUnavailable("The AI service is rate-limiting requests; try again later.") from e
        except anthropic.APIStatusError as e:
            raise AIUnavailable(f"The AI service returned an error ({e.status_code}).") from e
        except anthropic.APIConnectionError as e:
            raise AIUnavailable("Could not reach the AI service (network error or timeout).") from e

        usage = getattr(resp, "usage", None)
        tokens_in += getattr(usage, "input_tokens", 0) or 0
        tokens_out += getattr(usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise AIUnavailable(f"The AI declined to interpret this data (category: {cat}).")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            if resp.stop_reason == "max_tokens":
                raise ValueError("output was cut off at the token limit")
            data = json.loads(text)
            interp = AIInterpretation.model_validate(data)
            return AIResult(interp, getattr(resp, "model", s.ai_model), attempt, tokens_in, tokens_out)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_error = str(e)[:2000]
            log.warning("ai_output_invalid", attempt=attempt, error=last_error[:300])
            messages = messages + [
                # pass blocks back unchanged (thinking blocks must be preserved verbatim)
                {"role": "assistant", "content": [b.to_dict() if hasattr(b, "to_dict") else b for b in resp.content]},
                {"role": "user", "content": f"Your output was invalid: {last_error}\nReturn the complete corrected JSON object."},
            ]
    raise AIUnavailable(f"AI output failed validation after {attempts} attempt(s): {last_error[:300]}")
