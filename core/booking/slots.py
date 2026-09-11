"""core/booking/slots.py — availability windows → bookable slots (#23 B2 / ADR-0026).

`free_slots(user_id, context, day_from, day_to)` — two algorithms by context
(#233, Kai's explicit priority: friends see real freedom, guests see only
what she curates):

- **context == "friends"** (also admin — `_ctx_for` maps admin→friends):
  no manual windows at all. Any hour not covered by `busy_intervals()` is
  bookable, full stop — `_free_slots_any_time`.
- **context == "arcana"** (guest / public Arcana booking): the original
  windows model — weekly `booking_availability` rows (recurring by weekday,
  or one-off by `specific_date`, #232) → concrete slots (stepped by
  `slot_minutes`, meeting must fit before the window closes) → drop slots
  inside `min_notice_hours` / past `max_advance_days` → drop slots
  overlapping `busy_intervals()`, widened by the window's before/after
  buffers.

Everything is computed in UTC; the window's `tz` places its wall-clock hours.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

import sqlalchemy as sa

from core.booking.busy import busy_intervals, merge_intervals
from core.booking.tables import booking_availability

UTC = timezone.utc
_MSK = timezone(timedelta(hours=3))  # fallback

# #233: friends/admin ("friends" context) не настраивают окна вручную — им
# бронируемо ЛЮБОЕ время, свободное от дел (инверсия busy_intervals). Только
# guest/Arcana context ("arcana") остаётся на явных окнах booking_availability
# — приоритет по просьбе Кай: друзья видят реальную свободу, гости — только
# то, что она сама выставила.
_FRIENDS_SLOT_MINUTES = 60
_FRIENDS_MIN_NOTICE_HOURS = 2
_FRIENDS_MAX_ADVANCE_DAYS = 60


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
    booking_availability.c.specific_date,
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


async def _free_slots_any_time(
    user_id: str, day_from: date, day_to: date, now: datetime, engine,
) -> List[Slot]:
    """friends/admin: любой час, свободный от busy_intervals — без ручных окон."""
    step = timedelta(minutes=_FRIENDS_SLOT_MINUTES)
    day_start = datetime.combine(day_from, time(0, 0), tzinfo=_MSK).astimezone(UTC)
    day_end = datetime.combine(day_to + timedelta(days=1), time(0, 0), tzinfo=_MSK).astimezone(UTC)
    notice_cut = now + timedelta(hours=_FRIENDS_MIN_NOTICE_HOURS)
    advance_cut = now + timedelta(days=_FRIENDS_MAX_ADVANCE_DAYS)

    busy = merge_intervals(await busy_intervals(user_id, day_start, day_end, engine=engine))

    out: List[Slot] = []
    t = day_start
    while t + step <= day_end:
        if notice_cut <= t <= advance_cut and not any(a < t + step and b > t for a, b in busy):
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

    if context == "friends":
        return await _free_slots_any_time(user_id, day_from, day_to, now, engine)

    rows = await asyncio.to_thread(_rows_sync, engine, user_id, context)
    if not rows:
        return []

    # #232: ровно одно из weekday (повторяется каждую неделю) / specific_date
    # (разовое окно на конкретную дату) — CHECK в БД это гарантирует.
    by_weekday: dict = {}
    by_date: dict = {}
    for r in rows:
        if r.get("weekday") is not None:
            by_weekday.setdefault(int(r["weekday"]), []).append(r)
        elif r.get("specific_date") is not None:
            by_date.setdefault(r["specific_date"], []).append(r)

    candidates: List[tuple] = []  # (Slot, buffer_before, buffer_after)
    d = day_from
    while d <= day_to:
        for r in by_weekday.get(d.weekday(), []) + by_date.get(d, []):
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
