"""core/booking/tips.py — one deterministic booking hint from the owner's
availability windows (#23 / ADR-0026). No LLM.

The web sidebar (for friends / guests) shows a single friendly line like
"У Кай окна во второй половине дня — она не жаворонок". Rules only, derived
from `booking_availability`; a future version can fold in `shared`-tagged
Nexus memory facts.
"""
from __future__ import annotations

from typing import List, Optional

_DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _hhmm(v) -> Optional[int]:
    """→ minutes past midnight, or None."""
    if v is None:
        return None
    s = str(v)
    if ":" not in s:
        return None
    hh, mm = s.split(":")[:2]
    try:
        return int(hh) * 60 + int(mm)
    except ValueError:
        return None


def compute_tip(windows: List[dict], *, context: str = "friends") -> str:
    rows = [w for w in windows if w.get("context") == context and w.get("active", True)]
    if not rows:
        return "Окна под запись пока не настроены — напиши Кай напрямую."

    starts = [m for m in (_hhmm(w.get("start_time")) for w in rows) if m is not None]
    weekdays = {int(w["weekday"]) for w in rows if w.get("weekday") is not None}
    wk_only = weekdays and weekdays <= {5, 6}
    wd_only = weekdays and weekdays <= {0, 1, 2, 3, 4}

    if starts and min(starts) >= 12 * 60:
        base = "У Кай окна во второй половине дня — она не жаворонок, бронируй после обеда."
    elif starts and max(starts) < 12 * 60:
        base = "Утренние окна у Кай ловятся легче, чем вечерние."
    else:
        base = "У Кай окна и днём, и вечером — выбирай что удобно."

    if wk_only:
        return base + " Свободна в основном по выходным."
    if wd_only:
        return base + " По будням."
    if weekdays:
        days = ", ".join(_DOW[d] for d in sorted(weekdays))
        return base + f" Дни: {days}."
    return base
