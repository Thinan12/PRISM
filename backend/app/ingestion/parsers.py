"""Deterministic, Lao-aware value parsers.

Every parser takes a raw cell value (str / int / float / datetime / time / timedelta /
None) and returns either a parsed value or ``FAIL``. Parsers never guess silently: a
value that cannot be parsed unambiguously fails and is reported as a quality issue.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final


class _Fail:
    _inst = None

    def __new__(cls):  # singleton
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:  # pragma: no cover
        return "FAIL"

    def __bool__(self) -> bool:
        return False


FAIL: Final = _Fail()

# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

LAO_DIGITS = {ord(c): str(i) for i, c in enumerate("໐໑໒໓໔໕໖໗໘໙")}
THAI_DIGITS = {ord(c): str(i) for i, c in enumerate("๐๑๒๓๔๕๖๗๘๙")}
LAO_RANGE = re.compile(r"[຀-໿]")
NULL_TOKENS = {
    "", "-", "--", "—", "n/a", "na", "nan", "null", "none", "nil", "#n/a", "#value!",
    "#ref!", "#div/0!", "?", "ບໍ່ມີ", "ບໍ່ມີຂໍ້ມູນ",
}


def has_lao(text: str) -> bool:
    return bool(LAO_RANGE.search(text))


def normalize_text(value: Any) -> str | None:
    """Canonical text form: NFC, unified whitespace, stripped. None for empty."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    s = str(value)
    s = unicodedata.normalize("NFC", s)
    s = s.replace(" ", " ").replace("​", "").replace("﻿", "")
    s = re.sub(r"[ \t\r\f\v]+", " ", s).strip()
    return s if s else None


def is_null(value: Any) -> bool:
    s = normalize_text(value)
    return s is None or s.lower() in NULL_TOKENS


def ascii_digits(s: str) -> str:
    return s.translate(LAO_DIGITS).translate(THAI_DIGITS)


# ---------------------------------------------------------------------------
# Numbers & currency
# ---------------------------------------------------------------------------

CURRENCY_TOKENS = re.compile(
    r"(₭|lak|kip|ກີບ|usd|us\$|\$|thb|฿|บาท|ບາດ|ໂດລາ)", re.IGNORECASE
)
LAK_TOKENS = re.compile(r"(₭|\blak\b|\bkip\b|ກີບ)", re.IGNORECASE)
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_EU_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")
_SPACE_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}( \d{3})+([.,]\d+)?$")
_SCALE = {"k": 1_000, "m": 1_000_000, "mn": 1_000_000, "b": 1_000_000_000, "ລ້ານ": 1_000_000, "ພັນ": 1_000}


def detect_currency(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    m = CURRENCY_TOKENS.search(value)
    if not m:
        return None
    tok = m.group(1).lower()
    if tok in ("₭", "lak", "kip", "ກີບ"):
        return "LAK"
    if tok in ("usd", "us$", "$", "ໂດລາ"):
        return "USD"
    return "THB"


def parse_number(value: Any) -> float | int | _Fail:
    """Parse numbers incl. thousands separators, currency marks, Lao digits,
    accounting negatives ``(1,000)``, percentages and k/m scale suffixes."""
    if isinstance(value, bool):
        return FAIL
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return FAIL if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return float(value)
    if not isinstance(value, str):
        return FAIL
    s = normalize_text(value)
    if s is None:
        return FAIL
    s = ascii_digits(s)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    s = CURRENCY_TOKENS.sub("", s).strip()
    pct = s.endswith("%")
    if pct:
        s = s[:-1].strip()
    scale = 1
    m = re.match(r"^(.*?)(\s*)(k|m|mn|b|ລ້ານ|ພັນ)$", s, re.IGNORECASE)
    if m and m.group(1) and re.search(r"\d$", m.group(1)):
        scale = _SCALE[m.group(3).lower()]
        s = m.group(1).strip()
    if s.endswith("-") and s[:-1].replace(",", "").replace(".", "").isdigit():
        neg, s = True, s[:-1]
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("-"):
        neg, s = (not neg), s[1:].strip()
    if not s:
        return FAIL
    if _GROUPED_RE.match(s):
        s = s.replace(",", "")
    elif _SPACE_GROUPED_RE.match(s):
        s = s.replace(" ", "").replace(",", ".")
    elif _EU_GROUPED_RE.match(s) and s.count(".") >= 1 and ("," in s or s.count(".") > 1):
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d+,\d{1,2}$", s):  # decimal comma: 12,5
        s = s.replace(",", ".")
    if not _NUM_RE.match(s):
        return FAIL
    try:
        d = Decimal(s)
    except InvalidOperation:
        return FAIL
    d = d * scale
    if pct:
        d = d / 100
    if neg:
        d = -d
    if d == d.to_integral_value() and not pct and "." not in s and "e" not in s.lower():
        return int(d)
    return float(d)


# ---------------------------------------------------------------------------
# Booleans
# ---------------------------------------------------------------------------

TRUE_TOKENS = {"true", "yes", "y", "1", "ແມ່ນ", "ມີ", "ໄດ້", "✓", "✔", "x", "ok"}
FALSE_TOKENS = {"false", "no", "n", "0", "ບໍ່", "ບໍ່ແມ່ນ", "ບໍ່ໄດ້", "✗", "✘"}


def parse_bool(value: Any) -> bool | _Fail:
    if isinstance(value, bool):
        return value
    s = normalize_text(value)
    if s is None:
        return FAIL
    s = s.lower()
    if s in TRUE_TOKENS:
        return True
    if s in FALSE_TOKENS:
        return False
    return FAIL


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

LAO_MONTHS = {
    "ມັງກອນ": 1, "ກຸມພາ": 2, "ມີນາ": 3, "ເມສາ": 4, "ພຶດສະພາ": 5, "ມິຖຸນາ": 6,
    "ກໍລະກົດ": 7, "ສິງຫາ": 8, "ກັນຍາ": 9, "ຕຸລາ": 10, "ພະຈິກ": 11, "ທັນວາ": 12,
}
EN_MONTHS = {
    m: i + 1
    for i, ms in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")]
    )
    for m in ms
}
EXCEL_EPOCH = datetime(1899, 12, 30)

_ISO = re.compile(
    r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(am|pm)?)?(?:Z|[+-]\d{2}:?\d{2})?$",
    re.IGNORECASE,
)
_DMY = re.compile(
    r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?)?$",
    re.IGNORECASE,
)
_WORD = re.compile(r"^(\d{1,2})[\s\-/.,]*([^\d\s\-/.,]+)[\s\-/.,]*(\d{2}|\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_WORD_MDY = re.compile(r"^([^\d\s\-/.,]+)[\s\-/.,]*(\d{1,2})(?:st|nd|rd|th)?[\s,]+(\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


def _year(y: int) -> int:
    if y < 100:
        y += 2000 if y < 70 else 1900
    if y >= 2400:  # Buddhist Era (used in Laos & Thailand)
        y -= 543
    return y


def _mk(y: int, mo: int, d: int, hh=None, mi=None, ss=None, ampm=None) -> datetime | _Fail:
    try:
        h = int(hh) if hh else 0
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
        dt = datetime(_year(y), mo, d, h, int(mi) if mi else 0, int(ss) if ss else 0)
    except (ValueError, TypeError):
        return FAIL
    if not (1900 <= dt.year <= 2200):
        return FAIL
    return dt


def date_order_evidence(value: Any) -> str | None:
    """Return 'dmy' / 'mdy' if a slash-style date proves its order, else None."""
    s = normalize_text(value) if isinstance(value, str) else None
    if not s:
        return None
    m = _DMY.match(ascii_digits(s))
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a > 12 >= b:
        return "dmy"
    if b > 12 >= a:
        return "mdy"
    return None


def parse_date(value: Any, order: str = "dmy") -> datetime | _Fail:
    """Parse a date/datetime. ``order`` resolves ambiguous ``01/02/2025`` values and is
    decided per column from evidence (default day-first, the Lao convention)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, bool):
        return FAIL
    if isinstance(value, (int, float)):
        return FAIL  # bare numbers are only dates when explicitly treated as Excel serials
    s = normalize_text(value)
    if s is None:
        return FAIL
    s = ascii_digits(s)
    m = _ISO.match(s)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5), m.group(6), m.group(7))
    m = _DMY.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        d, mo = (a, b) if order == "dmy" else (b, a)
        if mo > 12 and d <= 12:  # value itself proves the other order
            d, mo = mo, d
        return _mk(y, mo, d, m.group(4), m.group(5), m.group(6), m.group(7))
    m = _WORD.match(s)
    if m:
        mo = LAO_MONTHS.get(m.group(2)) or EN_MONTHS.get(m.group(2).lower().rstrip("."))
        if mo:
            return _mk(int(m.group(3)), mo, int(m.group(1)), m.group(4), m.group(5), m.group(6))
    m = _WORD_MDY.match(s)
    if m:
        mo = EN_MONTHS.get(m.group(1).lower().rstrip("."))
        if mo:
            return _mk(int(m.group(3)), mo, int(m.group(2)), m.group(4), m.group(5), m.group(6))
    return FAIL


def excel_serial_to_datetime(n: float) -> datetime | _Fail:
    if not (1 <= n <= 109_575):  # 1900-01-01 .. 2199-12-31
        return FAIL
    return EXCEL_EPOCH + timedelta(days=float(n))


# ---------------------------------------------------------------------------
# Durations (call logs)
# ---------------------------------------------------------------------------

_HMS = re.compile(r"^(\d+):([0-5]?\d):([0-5]?\d)(?:\.\d+)?$")
_MS = re.compile(r"^(\d+):([0-5]\d)$")
_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|ຊົ່ວໂມງ|ຊມ|minutes?|mins?|m|ນາທີ|seconds?|secs?|s|ວິນາທີ|ວິ)",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "ຊົ່ວໂມງ": 3600, "ຊມ": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "ນາທີ": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "ວິນາທີ": 1, "ວິ": 1,
}


def parse_duration(value: Any) -> int | _Fail:
    """Return duration in whole seconds. Accepts hh:mm:ss, mm:ss, ``1h 2m 3s``,
    ``5 ນາທີ``, ``time``/``timedelta`` objects. Bare numbers are NOT durations
    (their unit is unknown) — the column typer decides that with header evidence."""
    if isinstance(value, timedelta):
        return int(round(value.total_seconds()))
    if isinstance(value, time):
        return value.hour * 3600 + value.minute * 60 + value.second
    if isinstance(value, datetime) and value.year in (1899, 1900) and value.month in (1, 12):
        # Excel durations > 24h come through as datetimes anchored at the epoch
        return int(round((value - EXCEL_EPOCH).total_seconds()))
    if not isinstance(value, str):
        return FAIL
    s = normalize_text(value)
    if s is None:
        return FAIL
    s = ascii_digits(s).lower()
    m = _HMS.match(s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = _MS.match(s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    total, pos, matched = 0.0, 0, False
    compact = s.replace(" ", "")
    for um in _UNIT.finditer(compact):
        if compact[pos:um.start()].strip(" ,"):
            return FAIL
        unit = um.group(2).lower()
        total += float(um.group(1)) * _UNIT_SECONDS[unit]
        pos, matched = um.end(), True
    if matched and not compact[pos:].strip(" ,"):
        return int(round(total))
    return FAIL


# ---------------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------------

_PHONE = re.compile(r"^\+?[\d\s\-().]{8,20}$")


def parse_phone(value: Any) -> str | _Fail:
    """Canonical Lao-friendly phone text. Keeps leading zeros; never numeric."""
    if isinstance(value, bool):
        return FAIL
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, float):
        if not value.is_integer():
            return FAIL
        value = str(int(value))
    if not isinstance(value, str):
        return FAIL
    s = normalize_text(value)
    if s is None:
        return FAIL
    s = ascii_digits(s)
    if not _PHONE.match(s):
        return FAIL
    digits = re.sub(r"\D", "", s)
    if not (8 <= len(digits) <= 13):
        return FAIL
    if s.startswith("+"):
        return "+" + digits
    return digits


def looks_like_lao_phone(digits: str) -> bool:
    d = digits.lstrip("+")
    if d.startswith("856"):
        d = "0" + d[3:]
    return bool(re.match(r"^0?(20|30)\d{7,8}$", d) or re.match(r"^0?21\d{6}$", d))
