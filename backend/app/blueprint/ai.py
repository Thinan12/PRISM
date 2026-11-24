"""AI-assisted semantic interpretation (local Ollama, OpenAI-compatible APIs or Claude).

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
import time
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
# Providers: one model call each; validation + retries are shared
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


@dataclass
class _Reply:
    text: str
    truncated: bool
    model: str
    input_tokens: int
    output_tokens: int
    assistant_message: dict[str, Any]  # appended to the conversation when a retry is needed


ClientFactory = Callable[[], Any]


def _default_client() -> Any:
    import anthropic

    s = get_settings()
    return anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.ai_timeout_seconds, max_retries=2)


def _default_http(timeout: float) -> Any:
    import httpx

    return httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))


client_factory: ClientFactory = _default_client  # Anthropic SDK client (patched in tests)
http_factory: Callable[[float], Any] = _default_http  # httpx client for Ollama / OpenAI-compatible (patched in tests)

PROVIDERS = ("ollama", "openai_compatible", "anthropic", "none")


def provider() -> str:
    s = get_settings()
    p = (s.ai_provider or "auto").strip().lower()
    if p == "auto":
        return "anthropic" if s.anthropic_api_key else "ollama"
    if p not in PROVIDERS:
        raise ValueError(f"PRISM_AI_PROVIDER must be one of auto, {', '.join(PROVIDERS)} (got '{p}').")
    return p


def model_name() -> str:
    s = get_settings()
    return {"anthropic": s.ai_model, "ollama": s.ollama_model, "openai_compatible": s.openai_model or "",
            "none": ""}[provider()]


def not_configured_reason() -> str | None:
    """None when the selected provider has the settings it needs (reachability is checked at call time)."""
    s = get_settings()
    p = provider()
    if p == "none":
        return "AI is switched off (PRISM_AI_PROVIDER=none)"
    if p == "anthropic" and not s.anthropic_api_key:
        return "AI is not configured (ANTHROPIC_API_KEY is not set)"
    if p == "ollama" and not (s.ollama_url and s.ollama_model):
        return "AI is not configured (PRISM_OLLAMA_URL / PRISM_OLLAMA_MODEL are not set)"
    if p == "openai_compatible" and not (s.openai_base_url and s.openai_model):
        return "AI is not configured (PRISM_OPENAI_BASE_URL / PRISM_OPENAI_MODEL are not set)"
    return None


def ai_configured() -> bool:
    return not_configured_reason() is None


def status() -> dict[str, Any]:
    """Provider status for the UI; for Ollama also whether the server answers and has the model."""
    p = provider()
    out: dict[str, Any] = {"provider": p, "model": model_name(), "configured": ai_configured(),
                           "reachable": None, "detail": not_configured_reason()}
    if p == "ollama" and out["configured"]:
        s = get_settings()
        try:
            with http_factory(3.0) as http:
                r = http.get(s.ollama_url.rstrip("/") + "/api/tags")
                r.raise_for_status()
                names = {m.get("name") for m in r.json().get("models", [])} | {m.get("model") for m in r.json().get("models", [])}
            want = s.ollama_model if ":" in s.ollama_model else s.ollama_model + ":latest"
            out["reachable"] = True
            if want not in names and s.ollama_model not in names:
                out["detail"] = f"Model '{s.ollama_model}' is not installed in Ollama — run: ollama pull {s.ollama_model}"
        except Exception:  # noqa: BLE001 - status must never fail
            out["reachable"] = False
            out["detail"] = f"Ollama is not reachable at {s.ollama_url}"
    return out


# array caps for grammar-constrained (local) decoding; None = number of columns in the payload
_ARRAY_CAPS: dict[str, int | None] = {
    "columns": None, "exclude_columns": None, "kpis": 8, "value_mappings": 10, "variants": 10,
    "filter_values": 10, "relationships": 10, "dashboard": 6, "kpi_names": 8, "assumptions": 8,
}
_STRING_CAPS = {"display_name": 60, "title": 80, "label": 80, "key": 80, "column": 80, "dataset_ref": 20}
_DEFAULT_STRING_CAP = 240


def bounded_schema(schema: dict[str, Any], n_datasets: int, max_columns: int) -> dict[str, Any]:
    """Inline refs and add maxItems/maxLength, so a grammar-constrained decoder cannot run away
    (small local models otherwise sometimes repeat items until the token limit)."""
    out = _inline_refs(schema)

    def walk(node: Any, name: str | None) -> None:
        if isinstance(node, dict):
            if node.get("type") == "array" and name:
                cap = n_datasets if name == "datasets" else _ARRAY_CAPS.get(name, 20)
                node["maxItems"] = max(1, cap if cap is not None else max_columns)
            if node.get("type") == "string" and "enum" not in node:
                node["maxLength"] = _STRING_CAPS.get(name or "", _DEFAULT_STRING_CAP)
            if "anyOf" in node:
                for v in node["anyOf"]:
                    walk(v, name)
            for k, v in (node.get("properties") or {}).items():
                walk(v, k)
            if "items" in node:
                walk(node["items"], name)
        return None

    walk(out, None)
    return out


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace local $ref/$defs with their definitions (grammar-based decoders handle plain schemas best)."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _anthropic_call(client: Any, messages: list[dict[str, Any]], schema: dict[str, Any]) -> _Reply:
    import anthropic

    s = get_settings()
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
    if resp.stop_reason == "refusal":
        cat = getattr(getattr(resp, "stop_details", None), "category", None)
        raise AIUnavailable(f"The AI declined to interpret this data (category: {cat}).")
    usage = getattr(resp, "usage", None)
    return _Reply(
        text="".join(b.text for b in resp.content if getattr(b, "type", "") == "text"),
        truncated=resp.stop_reason == "max_tokens",
        model=getattr(resp, "model", s.ai_model),
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        # pass blocks back unchanged (thinking blocks must be preserved verbatim)
        assistant_message={"role": "assistant", "content": [b.to_dict() if hasattr(b, "to_dict") else b for b in resp.content]},
    )


def _http_post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float, service: str) -> dict[str, Any]:
    import httpx

    try:
        with http_factory(timeout) as http:
            r = http.post(url, json=body, headers=headers)
    except httpx.TimeoutException as e:
        raise AIUnavailable(f"{service} did not answer within {int(timeout)} s.") from e
    except httpx.HTTPError as e:
        raise AIUnavailable(f"Could not reach {service} at {url.split('/api/')[0].split('/chat/')[0]}.") from e
    if r.status_code >= 400:
        try:
            err = r.json().get("error")
            msg = err.get("message") if isinstance(err, dict) else err
        except Exception:  # noqa: BLE001
            msg = None
        msg = str(msg or r.text or "")[:300]
        if r.status_code == 404 and "not found" in msg.lower():
            raise AIUnavailable(f"{service}: {msg}" + (" (run: ollama pull <model>)" if service == "Ollama" else ""))
        if r.status_code in (401, 403):
            raise AIUnavailable(f"{service} rejected the API key ({r.status_code}).")
        if r.status_code == 429:
            raise AIUnavailable(f"{service} is rate-limiting requests; try again later.")
        raise AIUnavailable(f"{service} returned an error ({r.status_code}): {msg}")
    try:
        return r.json()
    except ValueError as e:
        raise AIUnavailable(f"{service} returned a non-JSON response.") from e


def ollama_context_size(messages: list[dict[str, Any]], num_predict: int, maximum: int) -> int:
    """Smallest 4096-multiple window holding the prompt plus the output budget (bounded by ``maximum``).
    Prompt tokens are over-estimated at 1 token per 2.5 characters (Lao script tokenizes densely)."""
    chars = len(SYSTEM) + sum(len(m["content"]) if isinstance(m["content"], str) else len(json.dumps(m["content"]))
                              for m in messages)
    need = int(chars / 2.5) + num_predict + 512
    return max(4096, min(maximum, -(-need // 4096) * 4096))


PAYLOAD_MARKER = "\n\nPROFILES:\n"


def _payload_shape(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """(datasets, max columns) of the payload in the first user message."""
    try:
        data = json.loads(messages[0]["content"].split(PAYLOAD_MARKER, 1)[1])
        ds = data["datasets"]
        return len(ds), max(len(d["columns"]) for d in ds)
    except Exception:  # noqa: BLE001 - only used to tighten bounds
        return 20, 200


def _ollama_call(messages: list[dict[str, Any]], schema: dict[str, Any]) -> _Reply:
    s = get_settings()
    num_ctx = ollama_context_size(messages, s.ollama_num_predict, s.ollama_num_ctx)
    body = {
        "model": s.ollama_model,
        "messages": [{"role": "system", "content": SYSTEM}, *messages],
        "format": bounded_schema(schema, *_payload_shape(messages)),  # Ollama constrains decoding to this schema
        "stream": False,
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": s.ollama_num_predict},
    }
    data = _http_post(s.ollama_url.rstrip("/") + "/api/chat", body, {}, s.ollama_timeout_seconds, "Ollama")
    text = (data.get("message") or {}).get("content") or ""
    return _Reply(text=text, truncated=data.get("done_reason") == "length", model=f"ollama/{data.get('model', s.ollama_model)}",
                  input_tokens=data.get("prompt_eval_count") or 0, output_tokens=data.get("eval_count") or 0,
                  assistant_message={"role": "assistant", "content": text})


def _openai_call(messages: list[dict[str, Any]], schema: dict[str, Any]) -> _Reply:
    s = get_settings()
    system = SYSTEM
    if s.openai_json_mode == "object":
        fmt: dict[str, Any] = {"type": "json_object"}
        system += "\n\nReturn ONLY a JSON object that conforms to this JSON schema:\n" + json.dumps(_inline_refs(schema))
    else:
        fmt = {"type": "json_schema", "json_schema": {"name": "prism_interpretation", "strict": True, "schema": schema}}
    body = {"model": s.openai_model, "messages": [{"role": "system", "content": system}, *messages],
            "response_format": fmt, "temperature": 0, "max_tokens": 8000}
    headers = {"Authorization": f"Bearer {s.openai_api_key}"} if s.openai_api_key else {}
    data = _http_post(s.openai_base_url.rstrip("/") + "/chat/completions", body, headers, s.ai_timeout_seconds, "The AI service")
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise AIUnavailable("The AI service returned an unexpected response.") from e
    msg = choice.get("message") or {}
    if msg.get("refusal"):
        raise AIUnavailable("The AI declined to interpret this data.")
    text = msg.get("content") or ""
    usage = data.get("usage") or {}
    return _Reply(text=text, truncated=choice.get("finish_reason") == "length", model=data.get("model") or s.openai_model or "",
                  input_tokens=usage.get("prompt_tokens") or 0, output_tokens=usage.get("completion_tokens") or 0,
                  assistant_message={"role": "assistant", "content": text})


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return t


def interpret(payload: dict[str, Any], max_attempts: int | None = None) -> AIResult:
    """Ask the configured model for a schema-valid interpretation, or raise AIUnavailable."""
    s = get_settings()
    reason = not_configured_reason()
    if reason:
        raise AIUnavailable(reason + ".")
    p = provider()
    if p == "anthropic":
        client = client_factory()
        call: Callable[[list[dict[str, Any]], dict[str, Any]], _Reply] = lambda m, sc: _anthropic_call(client, m, sc)
    else:
        call = {"ollama": _ollama_call, "openai_compatible": _openai_call}[p]
    attempts = max_attempts or (s.ai_max_retries + 1)
    first = {
        "role": "user",
        "content": "Interpret these dataset profiles and return the JSON interpretation." + PAYLOAD_MARKER
                   + json.dumps(payload, ensure_ascii=False, default=str),
    }
    messages: list[dict[str, Any]] = [first]
    schema = api_schema()
    # stop retrying once one provider timeout's worth of wall-clock time has been spent
    budget = s.ollama_timeout_seconds if p == "ollama" else s.ai_timeout_seconds
    started = time.monotonic()
    tokens_in = tokens_out = 0
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        reply = call(messages, schema)
        tokens_in += reply.input_tokens
        tokens_out += reply.output_tokens
        try:
            if reply.truncated:
                raise ValueError("output was cut off at the token limit")
            interp = AIInterpretation.model_validate(json.loads(_strip_fences(reply.text)))
            return AIResult(interp, reply.model, attempt, tokens_in, tokens_out)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_error = str(e)[:2000]
            log.warning("ai_output_invalid", provider=p, attempt=attempt, error=last_error[:300])
            if time.monotonic() - started > budget:
                last_error += f" (stopped retrying after {int(time.monotonic() - started)} s)"
                break
            if reply.truncated:
                # start again instead of sending thousands of cut-off tokens back
                messages = [first | {"content": first["content"] + "\n\nYour previous answer was too long and was cut "
                                     "off. Keep every description and rationale under 20 words."}]
                continue
            messages = messages + [
                reply.assistant_message,
                {"role": "user", "content": f"Your output was invalid: {last_error}\nReturn the complete corrected JSON object."},
            ]
    raise AIUnavailable(f"AI output failed validation after {attempt} attempt(s): {last_error[:300]}")
