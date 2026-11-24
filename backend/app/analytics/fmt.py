"""Server-side value formatting for generated answer text (mirrors frontend/src/lib/format.ts)."""

from __future__ import annotations

import datetime as dt
from typing import Any

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def fmt_number(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    sign = "-" if s < 0 else ""
    s = abs(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{sec:02d}" if h else f"{sign}{m}:{sec:02d}"


def fmt_value(v: Any, fmt: str) -> str:
    if v is None:
        return "(blank)"
    if fmt == "lak" and isinstance(v, (int, float)):
        return f"{fmt_number(round(v)) if abs(v) >= 100 else fmt_number(v)} ₭"
    if fmt == "duration" and isinstance(v, (int, float)):
        return fmt_duration(v)
    if fmt == "percent" and isinstance(v, (int, float)):
        return f"{v * 100:.1f} %"
    if fmt == "boolean":
        return "yes" if v else "no"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return fmt_number(v)
    return str(v)


def fmt_period(v: Any, grain: str | None) -> str:
    if v is None:
        return "(no date)"
    try:
        d = dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return str(v)
    if grain == "month":
        return f"{MONTHS[d.month - 1]} {d.year}"
    if grain == "quarter":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if grain == "year":
        return str(d.year)
    if grain == "week":
        return f"week of {d.isoformat()}"
    return d.isoformat()


def fmt_change(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    return f"{'+' if pct >= 0 else '−'}{abs(pct) * 100:.1f} %"
