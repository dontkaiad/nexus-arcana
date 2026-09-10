"""core/booking/repo.py — CRUD for the `booking` table (#23 / ADR-0026).

Thin SQLAlchemy Core over `core.booking.tables.booking`. A booking row in a
holding status (`pending`/`confirmed`) is itself counted by
`busy_intervals()`, so creating the row is what reserves the slot — no
separate block/task needed for the calendar to be correct. Linking a
confirmed booking to a Nexus task / 🔮 Work is B6.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import sqlalchemy as sa

from core.booking.tables import BLOCKING_BOOKING_STATUSES, booking

_UTC = timezone.utc


@dataclass
class Booking:
    id: int
    context: str
    meeting_type_id: Optional[int]
    requester_tg_id: Optional[int]
    requester_name: str
    requester_contact: str
    start_at: datetime
    end_at: datetime
    hours: Optional[float]
    status: str
    note: str
    source: str
    token: str
    nexus_task_id: Optional[str]
    arcana_work_id: Optional[str]
    created_at: Optional[datetime]
    decided_at: Optional[datetime]


def _row_to_booking(r) -> Booking:
    m = r._mapping
    return Booking(
        id=m["id"], context=m["context"], meeting_type_id=m["meeting_type_id"],
        requester_tg_id=m["requester_tg_id"], requester_name=m["requester_name"] or "",
        requester_contact=m["requester_contact"] or "",
        start_at=m["start_at"], end_at=m["end_at"],
        hours=(float(m["hours"]) if m["hours"] is not None else None),
        status=m["status"], note=m["note"] or "", source=m["source"] or "web",
        token=m["token"], nexus_task_id=m["nexus_task_id"], arcana_work_id=m["arcana_work_id"],
        created_at=m["created_at"], decided_at=m["decided_at"],
    )


def _engine():
    from core.db import get_engine
    return get_engine()


def _create_sync(engine, **vals) -> Booking:
    vals.setdefault("token", secrets.token_urlsafe(24))
    with engine.begin() as conn:
        row = conn.execute(booking.insert().values(**vals).returning(booking)).first()
    return _row_to_booking(row)


def _get_sync(engine, *, booking_id: Optional[int] = None, token: str = "") -> Optional[Booking]:
    q = sa.select(booking)
    if booking_id is not None:
        q = q.where(booking.c.id == booking_id)
    elif token:
        q = q.where(booking.c.token == token)
    else:
        return None
    with engine.connect() as conn:
        row = conn.execute(q).first()
    return _row_to_booking(row) if row else None


def _list_sync(engine, user_id: str, statuses: Optional[tuple], upcoming_only: bool) -> List[Booking]:
    q = sa.select(booking)
    if user_id:
        q = q.where(booking.c.user_id.in_([user_id, ""]))
    if statuses:
        q = q.where(booking.c.status.in_(statuses))
    if upcoming_only:
        q = q.where(booking.c.end_at >= sa.func.now())
    q = q.order_by(booking.c.start_at)
    with engine.connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_booking(r) for r in rows]


def _set_status_sync(engine, booking_id: int, status: str) -> Optional[Booking]:
    with engine.begin() as conn:
        row = conn.execute(
            booking.update()
            .where(booking.c.id == booking_id)
            .values(status=status, decided_at=datetime.now(_UTC))
            .returning(booking)
        ).first()
    return _row_to_booking(row) if row else None


async def create_booking(
    *,
    user_id: str,
    context: str,
    start_at: datetime,
    end_at: datetime,
    status: str,
    hours: Optional[float] = None,
    meeting_type_id: Optional[int] = None,
    requester_tg_id: Optional[int] = None,
    requester_name: str = "",
    requester_contact: str = "",
    note: str = "",
    source: str = "web",
    engine=None,
) -> Booking:
    eng = engine or _engine()
    return await asyncio.to_thread(
        _create_sync, eng,
        user_id=user_id, context=context, start_at=start_at, end_at=end_at,
        status=status, hours=hours, meeting_type_id=meeting_type_id,
        requester_tg_id=requester_tg_id, requester_name=requester_name,
        requester_contact=requester_contact, note=note, source=source,
    )


async def get_booking(*, booking_id: Optional[int] = None, token: str = "", engine=None) -> Optional[Booking]:
    eng = engine or _engine()
    return await asyncio.to_thread(_get_sync, eng, booking_id=booking_id, token=token)


async def list_bookings(
    user_id: str = "",
    *,
    statuses: Optional[tuple] = None,
    upcoming_only: bool = True,
    engine=None,
) -> List[Booking]:
    eng = engine or _engine()
    return await asyncio.to_thread(_list_sync, eng, user_id, statuses, upcoming_only)


async def set_booking_status(booking_id: int, status: str, *, engine=None) -> Optional[Booking]:
    eng = engine or _engine()
    return await asyncio.to_thread(_set_status_sync, eng, booking_id, status)


__all__ = [
    "Booking", "BLOCKING_BOOKING_STATUSES",
    "create_booking", "get_booking", "list_bookings", "set_booking_status",
]
