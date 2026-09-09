"""core/booking/busy.py — free/busy aggregator for Lark Booking (#23 / ADR-0026).

`busy_intervals(user_id, start, end)` returns every interval the owner is
committed during `[start, end)`, from four internal sources:

- **Nexus tasks** — any active (not Done/Archived) task with a `deadline`
  counts as a 1-hour meeting at that time. Kai: "по дедлайнам все мои
  задачи маркируются как встречи". A task with only a `reminder` does NOT
  block (a reminder is a poke, not a commitment).
- **Arcana works** — an active work with `scheduled_at` set (a forward-looking
  esoteric appointment), 1 hour.
- **Confirmed / held bookings** — `booking` rows in a slot-holding status
  (`pending`/`confirmed`), using their real `start_at`/`end_at`.
- **Manual blocks** — `booking_block` rows (vacation, "не беспокоить").

The availability-window → bookable-slots layer sits on top of this and lands
in a later phase (B2 backend / B5 web).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import sqlalchemy as sa

from arcana.repos.works_tables import works, work_status
from core.booking.tables import BLOCKING_BOOKING_STATUSES, booking, booking_block
from nexus.repos.tasks_tables import tasks, task_status

# A Nexus task / Arcana work is a point in time; treat it as this long.
DEFAULT_BUSY_MINUTES = 60

_DONE_CODES = ("Done", "Archived")


@dataclass
class BusyInterval:
    start: datetime          # tz-aware, UTC
    end: datetime            # tz-aware, UTC
    source: str              # 'task' | 'work' | 'booking' | 'block'
    label: str = ""          # title / reason — hide from non-admin viewers
    ref_id: str = ""

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and self.end > start


def _as_utc(value) -> Optional[datetime]:
    """Normalize a DB datetime/ISO string to a tz-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(s)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _busy_sync(engine, user_id: str, start: datetime, end: datetime) -> List[BusyInterval]:
    start = _as_utc(start)
    end = _as_utc(end)
    if start is None or end is None or start >= end:
        return []

    dur = timedelta(minutes=DEFAULT_BUSY_MINUTES)
    # widen the point-event fetch so an event starting just before `start`
    # whose 1h tail reaches into the window is still caught
    fetch_from = start - dur
    out: List[BusyInterval] = []

    with engine.connect() as conn:
        # ── Nexus tasks with a deadline ─────────────────────────────────────
        done_ids = sa.select(task_status.c.id).where(task_status.c.code.in_(_DONE_CODES))
        q = (
            sa.select(tasks.c.id, tasks.c.title, tasks.c.deadline)
            .where(tasks.c.status_id.notin_(done_ids))
            .where(tasks.c.deadline.isnot(None))
            .where(tasks.c.deadline >= fetch_from)
            .where(tasks.c.deadline < end)
        )
        if user_id:
            q = q.where(tasks.c.user_id.in_([user_id, ""]))
        for row in conn.execute(q):
            d = _as_utc(row.deadline)
            if d is None:
                continue
            iv = BusyInterval(d, d + dur, "task", row.title or "Задача", str(row.id))
            if iv.overlaps(start, end):
                out.append(iv)

        # ── Arcana works with scheduled_at ─────────────────────────────────
        wdone = sa.select(work_status.c.id).where(work_status.c.code.in_(_DONE_CODES))
        wq = (
            sa.select(works.c.id, works.c.title, works.c.scheduled_at)
            .where(works.c.scheduled_at.isnot(None))
            .where(works.c.scheduled_at >= fetch_from)
            .where(works.c.scheduled_at < end)
        )
        # work_status may lack the Done/Archived codes on older DBs — notin_ of an
        # empty subquery is still fine
        wq = wq.where(works.c.status_id.notin_(wdone))
        if user_id:
            wq = wq.where(works.c.user_id.in_([user_id, ""]))
        for row in conn.execute(wq):
            s = _as_utc(row.scheduled_at)
            if s is None:
                continue
            iv = BusyInterval(s, s + dur, "work", row.title or "Работа", str(row.id))
            if iv.overlaps(start, end):
                out.append(iv)

        # ── Bookings holding a slot ────────────────────────────────────────
        bq = (
            sa.select(booking.c.id, booking.c.start_at, booking.c.end_at,
                      booking.c.requester_name, booking.c.status)
            .where(booking.c.status.in_(BLOCKING_BOOKING_STATUSES))
            .where(booking.c.start_at < end)
            .where(booking.c.end_at > start)
        )
        if user_id:
            bq = bq.where(booking.c.user_id.in_([user_id, ""]))
        for row in conn.execute(bq):
            bs, be = _as_utc(row.start_at), _as_utc(row.end_at)
            if bs is None or be is None:
                continue
            out.append(BusyInterval(bs, be, "booking", row.requester_name or "", str(row.id)))

        # ── Manual blocks ─────────────────────────────────────────────────
        kq = (
            sa.select(booking_block.c.id, booking_block.c.start_at,
                      booking_block.c.end_at, booking_block.c.reason)
            .where(booking_block.c.start_at < end)
            .where(booking_block.c.end_at > start)
        )
        if user_id:
            kq = kq.where(booking_block.c.user_id.in_([user_id, ""]))
        for row in conn.execute(kq):
            ks, ke = _as_utc(row.start_at), _as_utc(row.end_at)
            if ks is None or ke is None:
                continue
            out.append(BusyInterval(ks, ke, "block", row.reason or "", str(row.id)))

    out.sort(key=lambda i: (i.start, i.end))
    return out


def merge_intervals(intervals: List[BusyInterval]) -> List[tuple]:
    """Collapse a sorted BusyInterval list into merged (start, end) tuples —
    the shape the slot computation and the public (opaque) view want."""
    merged: List[tuple] = []
    for iv in sorted(intervals, key=lambda i: (i.start, i.end)):
        if merged and iv.start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], iv.end))
        else:
            merged.append((iv.start, iv.end))
    return merged


async def busy_intervals(
    user_id: str,
    start: datetime,
    end: datetime,
    *,
    engine=None,
) -> List[BusyInterval]:
    """All intervals the owner is committed during [start, end), sorted."""
    if engine is None:
        from core.db import get_engine
        engine = get_engine()
    return await asyncio.to_thread(_busy_sync, engine, user_id, start, end)
