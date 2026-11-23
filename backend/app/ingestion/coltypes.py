"""Deterministic column type inference and conversion.

Produces typed values plus a per-column record of every value that could not be
converted (kept in the raw layer, reported as a quality issue — never discarded).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.ingestion.parsers import (
    FAIL,
    LAK_TOKENS,
    date_order_evidence,
    detect_currency,
    excel_serial_to_datetime,
    has_lao,
    is_null,
    looks_like_lao_phone,
    normalize_text,
    parse_bool,
    parse_date,
    parse_duration,
    parse_number,
    parse_phone,
)

LOGICAL_TYPES = ("text", "integer", "number", "boolean", "date", "datetime", "duration", "phone", "empty")

HINT_DATE = re.compile(r"(date|day|time|created|updated|ວັນທີ|ວັນ|ເວລາ|ມື້)", re.IGNORECASE)
HINT_DURATION = re.compile(r"(duration|\bdur\b|length|talk ?time|call ?length|ໄລຍະເວລາ|ໄລຍະການໂທ)", re.IGNORECASE)
HINT_SECONDS = re.compile(r"(sec|second|\(s\)|ວິນາທີ)", re.IGNORECASE)
HINT_MINUTES = re.compile(r"(min|minute|\(m\)|ນາທີ)", re.IGNORECASE)
HINT_PHONE = re.compile(r"(phone|tel|mobile|msisdn|caller|callee|number|ເບີ|ໂທລະສັບ|ເບີໂທ)", re.IGNORECASE)
HINT_LAK = re.compile(r"(\blak\b|kip|ກີບ|₭)", re.IGNORECASE)
HINT_ID = re.compile(r"(^id$|\bid\b|_id$|code|no\.?$|ລະຫັດ|ລຳດັບ|ລ/ດ)", re.IGNORECASE)

MIN_RATIO = 0.9


@dataclass
class ColumnResult:
    key: str
    label: str
    original_label: str | None
    letter: str
    logical_type: str
    values: list[Any]  # converted values (None for null/failed)
    raw: list[str | None]  # normalized raw text
    unit: str | None = None  # e.g. LAK, seconds
    date_order: str | None = None
    semantic_hint: str | None = None
    failures: list[tuple[int, str]] = field(default_factory=list)  # (row index, raw text)
    notes: list[str] = field(default_factory=list)
    trimmed: int = 0
    has_lao: bool = False

    def meta(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "original_label": self.original_label,
            "source_column": self.letter,
            "logical_type": self.logical_type,
            "unit": self.unit,
            "date_order": self.date_order,
            "semantic_hint": self.semantic_hint,
            "has_lao": self.has_lao,
            "parse_failures": len(self.failures),
            "notes": self.notes,
        }


def make_key(label: str, index: int, used: set[str]) -> str:
    s = unicodedata.normalize("NFKD", label)
    s = "".join(ch for ch in s if ord(ch) < 128)
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    if not s or s[0].isdigit() or len(s) < 2:
        s = f"col_{index + 1}" if not s else f"c_{s}"
    s = s[:48]
    base, n = s, 2
    while s in used or s.startswith("_"):
        s = f"{base}_{n}"
        n += 1
    used.add(s)
    return s


def _ratio_ok(ok: int, n: int) -> bool:
    if n == 0:
        return False
    if n < 5:
        return ok == n
    return ok / n >= MIN_RATIO


def _raw_text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ") if (v.hour or v.minute or v.second) else v.date().isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return normalize_text(v)


def infer_column(
    label: str,
    original_label: str | None,
    letter: str,
    cells: list[Any],
    formats: list[str | None] | None,
    key: str,
) -> ColumnResult:
    raw = [_raw_text(v) for v in cells]
    trimmed = sum(
        1 for v in cells if isinstance(v, str) and normalize_text(v) is not None and normalize_text(v) != v
    )
    present = [(i, v) for i, v in enumerate(cells) if not is_null(v)]
    res = ColumnResult(key=key, label=label, original_label=original_label, letter=letter,
                       logical_type="text", values=[None] * len(cells), raw=raw, trimmed=trimmed)
    res.has_lao = any(isinstance(v, str) and has_lao(v) for _, v in present[:2000])
    n = len(present)
    if n == 0:
        res.logical_type = "empty"
        return res
    vals = [v for _, v in present]
    hdr = f"{label} {original_label or ''}"
    fmt_text = " ".join(f for f in (formats or []) if f)

    def attempt(fn) -> tuple[int, list[Any]]:
        out, ok = [], 0
        for v in vals:
            r = fn(v)
            if r is FAIL:
                out.append(FAIL)
            else:
                ok += 1
                out.append(r)
        return ok, out

    def commit(ltype: str, parsed: list[Any], **kw) -> ColumnResult:
        res.logical_type = ltype
        for (i, v), p in zip(present, parsed):
            if p is FAIL:
                res.failures.append((i, _raw_text(v) or ""))
            else:
                res.values[i] = p
        for k, v in kw.items():
            setattr(res, k, v)
        return res

    is_time_obj = sum(isinstance(v, (time, timedelta)) for v in vals)
    duration_hint = bool(HINT_DURATION.search(hdr))
    time_of_day_hint = bool(HINT_DATE.search(hdr)) and not duration_hint
    str_vals = [v for v in vals if isinstance(v, str)]

    # 1. durations
    ok, parsed = attempt(parse_duration)
    if _ratio_ok(ok, n):
        clock_like = all(
            isinstance(v, time) or (isinstance(v, str) and re.fullmatch(r"\s*\d{1,2}:\d{2}(:\d{2})?\s*", v))
            for v in vals
        )
        if time_of_day_hint and clock_like:
            res.logical_type = "text"
            res.semantic_hint = "time_of_day"
            for i, v in present:
                res.values[i] = v.strftime("%H:%M:%S") if isinstance(v, time) else normalize_text(v)
            res.notes.append("Clock times kept as time-of-day text (header indicates a time, not a duration).")
            return res
        if not is_time_obj:
            res.notes.append("Durations normalized to seconds.")
        return commit("duration", parsed, unit="seconds")
    if duration_hint:
        ok, nums = attempt(parse_number)
        if _ratio_ok(ok, n):
            factor = 60 if HINT_MINUTES.search(hdr) and not HINT_SECONDS.search(hdr) else 1
            conv = [FAIL if p is FAIL or p < 0 else int(round(p * factor)) for p in nums]
            if factor == 1 and not HINT_SECONDS.search(hdr):
                res.notes.append("Numeric durations assumed to be in seconds (header gives no unit).")
            elif factor == 60:
                res.notes.append("Numeric durations converted from minutes to seconds.")
            return commit("duration", conv, unit="seconds")

    # 2. dates
    date_hint = bool(HINT_DATE.search(hdr))
    native_dates = sum(isinstance(v, (datetime, date)) and not isinstance(v, time) for v in vals)
    ev = [date_order_evidence(v) for v in str_vals[:20000]]
    dmy, mdy = ev.count("dmy"), ev.count("mdy")
    order = "mdy" if mdy > dmy else "dmy"
    ok, parsed = attempt(lambda v: parse_date(v, order))
    if _ratio_ok(ok, n) and (native_dates or str_vals):
        if dmy and mdy:
            res.notes.append(f"Mixed day/month order detected ({dmy} day-first vs {mdy} month-first values); used {'day' if order == 'dmy' else 'month'}-first.")
        elif not dmy and not mdy and any(re.match(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", v) for v in str_vals):
            res.notes.append("Ambiguous dates interpreted day-first (Lao convention).")
        dts = [p for p in parsed if p is not FAIL]
        ltype = "datetime" if any(p.hour or p.minute or p.second for p in dts) else "date"
        return commit(ltype, parsed, date_order=order)
    if date_hint and all(isinstance(v, (int, float)) for v in vals):
        ok, parsed = attempt(lambda v: excel_serial_to_datetime(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else FAIL)
        if _ratio_ok(ok, n) and all(20_000 < v < 80_000 for v in vals if isinstance(v, (int, float))):
            res.notes.append("Numbers converted from Excel date serials.")
            dts = [p for p in parsed if p is not FAIL]
            ltype = "datetime" if any(p.hour or p.minute for p in dts) else "date"
            return commit(ltype, parsed)

    # 3. phones (before numbers so leading zeros are never lost)
    phone_hint = bool(HINT_PHONE.search(hdr))
    ok, parsed = attempt(parse_phone)
    if _ratio_ok(ok, n):
        lead = sum(1 for v in vals if isinstance(v, str) and normalize_text(v).lstrip().startswith(("0", "+")))
        laoish = sum(1 for p in parsed if p is not FAIL and looks_like_lao_phone(p))
        if phone_hint or lead / n >= 0.5 or laoish / n >= 0.8:
            return commit("phone", parsed, semantic_hint="phone")

    # 4. booleans (string tokens only; a 0/1 numeric column stays numeric)
    if str_vals and len(str_vals) == n:
        ok, parsed = attempt(parse_bool)
        distinct = {normalize_text(v).lower() for v in vals}
        if _ratio_ok(ok, n) and len(distinct) <= 4 and not distinct <= {"0", "1"}:
            return commit("boolean", parsed)

    # 5. numbers / currency
    ok, parsed = attempt(parse_number)
    if _ratio_ok(ok, n):
        unit = None
        lak_vals = sum(1 for v in str_vals if LAK_TOKENS.search(v))
        cur = {detect_currency(v) for v in str_vals} - {None}
        if lak_vals or HINT_LAK.search(hdr) or re.search(r"₭|LAK|kip|ກີບ", fmt_text, re.IGNORECASE):
            unit = "LAK"
        elif len(cur) == 1:
            unit = cur.pop()
        elif len(cur) > 1:
            res.notes.append(f"Mixed currencies in column: {', '.join(sorted(cur))}.")
        pct = sum(1 for v in str_vals if v.strip().endswith("%")) or ("%" in fmt_text)
        if pct:
            unit = "percent"
        nums = [p for p in parsed if p is not FAIL]
        ltype = "integer" if all(isinstance(p, int) for p in nums) else "number"
        if ltype == "integer" and HINT_ID.search(hdr):
            res.semantic_hint = "identifier"
        return commit(ltype, parsed, unit=unit)

    # 6. text
    res.logical_type = "text"
    for i, v in present:
        res.values[i] = _raw_text(v)
    distinct = len({res.values[i] for i, _ in present})
    if distinct <= max(25, int(0.05 * n)) and distinct < n:
        res.semantic_hint = "category"
    if trimmed:
        res.notes.append(f"Trimmed surrounding whitespace in {trimmed} value(s).")
    return res
