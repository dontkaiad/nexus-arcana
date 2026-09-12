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
from datetime import date, datetime, time, timezone
from typing import List, Optional

import sqlalchemy as sa

from core.booking.tables import (
    BLOCKING_BOOKING_STATUSES, booking, booking_availability, booking_block,
    booking_meeting_type,
)

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
    user_id: str = ""  # owner key — appended (kept last so callers that omit it still work)


def _row_to_booking(r) -> Booking:
    m = r._mapping
    return Booking(
        id=m["id"], user_id=m["user_id"] or "", context=m["context"],
        meeting_type_id=m["meeting_type_id"],
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


def _reschedule_sync(engine, booking_id: int, start_at: datetime, end_at: datetime) -> Optional[Booking]:
    with engine.begin() as conn:
        row = conn.execute(
            booking.update()
            .where(booking.c.id == booking_id)
            .values(start_at=start_at, end_at=end_at)
            .returning(booking)
        ).first()
    return _row_to_booking(row) if row else None


def _set_link_sync(engine, booking_id: int, *, nexus_task_id=None, arcana_work_id=None) -> Optional[Booking]:
    vals = {}
    if nexus_task_id is not None:
        vals["nexus_task_id"] = str(nexus_task_id)
    if arcana_work_id is not None:
        vals["arcana_work_id"] = str(arcana_work_id)
    if not vals:
        return None
    with engine.begin() as conn:
        row = conn.execute(
            booking.update().where(booking.c.id == booking_id).values(**vals).returning(booking)
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


async def reschedule_booking(
    booking_id: int, start_at: datetime, end_at: datetime, *, engine=None,
) -> Optional[Booking]:
    """#220 (B7): admin переносит бронь на другое время. Не трогает status —
    линковка (Nexus-задача / 🔮 Работа) обновляется отдельно вызывающим
    кодом через core.booking.linkage.update_linked_time."""
    eng = engine or _engine()
    return await asyncio.to_thread(_reschedule_sync, eng, booking_id, start_at, end_at)


async def set_booking_link(
    booking_id: int, *, nexus_task_id=None, arcana_work_id=None, engine=None
) -> Optional[Booking]:
    """Store the linked Nexus task / 🔮 Work id back on the booking row (B6)."""
    eng = engine or _engine()
    return await asyncio.to_thread(
        _set_link_sync, eng, booking_id,
        nexus_task_id=nexus_task_id, arcana_work_id=arcana_work_id,
    )


# ── config tables: availability windows / meeting types / manual blocks ──────
# Thin dict-in/dict-out CRUD — the owner-only admin API is the only caller.

_AVAIL_FIELDS = {
    "context", "weekday", "specific_date", "start_time", "end_time", "tz",
    "slot_minutes", "min_notice_hours", "max_advance_days",
    "buffer_before_min", "buffer_after_min", "active",
}
_MT_FIELDS = {
    "context", "slug", "title", "duration_min", "location_kind",
    "location_value", "requires_approval", "description", "color", "active",
}


def _rows(engine, table, user_id: str) -> List[dict]:
    q = sa.select(table)
    if user_id and "user_id" in table.c:
        q = q.where(table.c.user_id.in_([user_id, ""]))
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q.order_by(table.c.id))]


def _insert(engine, table, user_id: str, vals: dict, allowed: set) -> dict:
    clean = {k: v for k, v in vals.items() if k in allowed}
    clean["user_id"] = user_id
    with engine.begin() as conn:
        row = conn.execute(table.insert().values(**clean).returning(table)).first()
    return dict(row._mapping)


def _update(engine, table, row_id: int, user_id: str, vals: dict, allowed: set) -> Optional[dict]:
    clean = {k: v for k, v in vals.items() if k in allowed}
    if not clean:
        return None
    if "updated_at" in table.c:
        clean["updated_at"] = datetime.now(_UTC)
    q = table.update().where(table.c.id == row_id)
    if user_id and "user_id" in table.c:
        q = q.where(table.c.user_id.in_([user_id, ""]))
    with engine.begin() as conn:
        row = conn.execute(q.values(**clean).returning(table)).first()
    return dict(row._mapping) if row else None


def _delete(engine, table, row_id: int, user_id: str) -> bool:
    q = table.delete().where(table.c.id == row_id)
    if user_id and "user_id" in table.c:
        q = q.where(table.c.user_id.in_([user_id, ""]))
    with engine.begin() as conn:
        return conn.execute(q).rowcount > 0


def _coerce_time(vals: dict) -> dict:
    out = dict(vals)
    for k in ("start_time", "end_time"):
        v = out.get(k)
        if isinstance(v, str) and ":" in v:
            hh, mm = v.split(":")[:2]
            out[k] = time(int(hh), int(mm))
    v = out.get("specific_date")
    if isinstance(v, str) and v:
        out["specific_date"] = date.fromisoformat(v)
    return out


async def list_availability(user_id: str, *, engine=None) -> List[dict]:
    rows = await asyncio.to_thread(_rows, engine or _engine(), booking_availability, user_id)
    for r in rows:  # times → "HH:MM" for JSON
        for k in ("start_time", "end_time"):
            if isinstance(r.get(k), time):
                r[k] = r[k].strftime("%H:%M")
    return rows


async def add_availability(user_id: str, vals: dict, *, engine=None) -> dict:
    return await asyncio.to_thread(_insert, engine or _engine(), booking_availability, user_id, _coerce_time(vals), _AVAIL_FIELDS)


async def edit_availability(row_id: int, user_id: str, vals: dict, *, engine=None) -> Optional[dict]:
    return await asyncio.to_thread(_update, engine or _engine(), booking_availability, row_id, user_id, _coerce_time(vals), _AVAIL_FIELDS)


async def del_availability(row_id: int, user_id: str, *, engine=None) -> bool:
    return await asyncio.to_thread(_delete, engine or _engine(), booking_availability, row_id, user_id)


async def list_meeting_types(user_id: str, *, engine=None) -> List[dict]:
    return await asyncio.to_thread(_rows, engine or _engine(), booking_meeting_type, user_id)


async def add_meeting_type(user_id: str, vals: dict, *, engine=None) -> dict:
    return await asyncio.to_thread(_insert, engine or _engine(), booking_meeting_type, user_id, vals, _MT_FIELDS)


async def edit_meeting_type(row_id: int, user_id: str, vals: dict, *, engine=None) -> Optional[dict]:
    return await asyncio.to_thread(_update, engine or _engine(), booking_meeting_type, row_id, user_id, vals, _MT_FIELDS)


async def del_meeting_type(row_id: int, user_id: str, *, engine=None) -> bool:
    return await asyncio.to_thread(_delete, engine or _engine(), booking_meeting_type, row_id, user_id)


async def list_blocks(user_id: str, *, engine=None) -> List[dict]:
    return await asyncio.to_thread(_rows, engine or _engine(), booking_block, user_id)


async def add_block(user_id: str, start_at: datetime, end_at: datetime, reason: str = "", *, engine=None) -> dict:
    def _go(eng):
        with eng.begin() as conn:
            row = conn.execute(booking_block.insert().values(
                user_id=user_id, start_at=start_at, end_at=end_at, reason=reason,
            ).returning(booking_block)).first()
        return dict(row._mapping)
    eng = engine or _engine()
    block = await asyncio.to_thread(_go, eng)
    # #249: блок сам по себе уже занимает слоты (busy_intervals source='block')
    # — линкованная задача нужна ТОЛЬКО чтобы Кай видела его в «Мой день»
    # (она ориентируется на Nexus, не на страницу букинга). Никогда не роняет
    # создание блока — см. link_block.
    from core.booking.linkage import link_block
    return await link_block(block, engine=eng)


async def del_block(row_id: int, user_id: str, *, engine=None) -> bool:
    eng = engine or _engine()
    block = await asyncio.to_thread(_rows, eng, booking_block, user_id)
    task_id = next((b.get("nexus_task_id") for b in block if b["id"] == row_id), None)
    deleted = await asyncio.to_thread(_delete, eng, booking_block, row_id, user_id)
    if deleted and task_id:
        from core.booking.linkage import unlink_block
        await unlink_block(task_id, engine=eng)
    return deleted


__all__ = [
    "Booking", "BLOCKING_BOOKING_STATUSES",
    "create_booking", "get_booking", "list_bookings", "set_booking_status",
    "reschedule_booking",
    "list_availability", "add_availability", "edit_availability", "del_availability",
    "list_meeting_types", "add_meeting_type", "edit_meeting_type", "del_meeting_type",
    "list_blocks", "add_block", "del_block", "set_booking_link",
]
