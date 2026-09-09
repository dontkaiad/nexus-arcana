"""core/booking/slots.py — availability windows → bookable slots (#23 B2 / ADR-0026).

`free_slots(user_id, context, day_from, day_to)`:
  weekly `booking_availability` windows for the context
    → concrete slots (stepped by `slot_minutes`, meeting must fit before the
      window closes)
    → drop slots inside `min_notice_hours` / past `max_advance_days`
    → drop slots overlapping any `busy_intervals()` interval, widened by the
      window's before/after buffers

Everything is computed in UTC; the window's `tz` places its wall-clock hours.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

import sqlalchemy as sa

from core.booking.busy import busy_intervals
from core.booking.tables import booking_availability

UTC = timezone.utc
_MSK = timezone(timedelta(hours=3))  # fallback


@dataclass(frozen=True)
class Slot:
    start: datetime  # tz-aware, UTC
    end: datetime

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def _resolve_tz(name: str):
    if not name:
        return _MSK
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    m = name.strip()
    if m.startswith(("+", "-")) and ":" in m:
        try:
            sign = 1 if m[0] == "+" else -1
            hh, mm = m[1:].split(":")
            return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
        except Exception:
            pass
    return _MSK


def _as_time(value) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1] or 0))
    raise TypeError(f"bad time value: {value!r}")


_AVAIL_COLS = (
    booking_availability.c.weekday,
    booking_availability.c.start_time,
    booking_availability.c.end_time,
    booking_availability.c.tz,
    booking_availability.c.slot_minutes,
    booking_availability.c.min_notice_hours,
    booking_availability.c.max_advance_days,
    booking_availability.c.buffer_before_min,
    booking_availability.c.buffer_after_min,
)


def _rows_sync(engine, user_id: str, context: str):
    q = (
        sa.select(*_AVAIL_COLS)
        .where(booking_availability.c.context == context)
        .where(booking_availability.c.active == True)  # noqa: E712 (SQLAlchemy)
    )
    if user_id:
        q = q.where(booking_availability.c.user_id.in_([user_id, ""]))
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q)]


def _slots_for_window(row: dict, day: date, now: datetime) -> List[Slot]:
    tz = _resolve_tz(row.get("tz") or "")
    start_t = _as_time(row["start_time"])
    end_t = _as_time(row["end_time"])
    slot_min = int(row.get("slot_minutes") or 60)
    step = timedelta(minutes=slot_min)

    win_start = datetime.combine(day, start_t, tzinfo=tz).astimezone(UTC)
    win_end = datetime.combine(day, end_t, tzinfo=tz).astimezone(UTC)
    if win_end <= win_start:
        return []

    notice_cut = now + timedelta(hours=int(row.get("min_notice_hours") or 0))
    advance_cut = now + timedelta(days=int(row.get("max_advance_days") or 3650))

    out: List[Slot] = []
    t = win_start
    while t + step <= win_end:
        if notice_cut <= t <= advance_cut:
            out.append(Slot(t, t + step))
        t += step
    return out


async def free_slots(
    user_id: str,
    context: str,
    *,
    day_from: date,
    day_to: date,
    now: Optional[datetime] = None,
    engine=None,
) -> List[Slot]:
    if engine is None:
        from core.db import get_engine
        engine = get_engine()
    if now is None:
        now = datetime.now(UTC)
    now = now.astimezone(UTC)
    if day_to < day_from:
        return []

    rows = await asyncio.to_thread(_rows_sync, engine, user_id, context)
    if not rows:
        return []

    by_weekday: dict = {}
    for r in rows:
        by_weekday.setdefault(int(r["weekday"]), []).append(r)

    candidates: List[tuple] = []  # (Slot, buffer_before, buffer_after)
    d = day_from
    while d <= day_to:
        for r in by_weekday.get(d.weekday(), []):
            bb = int(r.get("buffer_before_min") or 0)
            ba = int(r.get("buffer_after_min") or 0)
            for s in _slots_for_window(r, d, now):
                candidates.append((s, bb, ba))
        d += timedelta(days=1)

    if not candidates:
        return []

    span_start = min(s.start for s, _, _ in candidates) - timedelta(hours=6)
    span_end = max(s.end for s, _, _ in candidates) + timedelta(hours=6)
    busy = await busy_intervals(user_id, span_start, span_end, engine=engine)

    free: List[Slot] = []
    seen = set()
    for s, bb, ba in sorted(candidates, key=lambda c: c[0].start):
        if s.start in seen:
            continue
        seen.add(s.start)
        check_start = s.start - timedelta(minutes=bb)
        check_end = s.end + timedelta(minutes=ba)
        if any(iv.overlaps(check_start, check_end) for iv in busy):
            continue
        free.append(s)
    return free
