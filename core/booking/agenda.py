"""core/booking/agenda.py — read-only «что у Кай по дням» для Зари (admin only).

Только таблицы, уже лежащие в образе Зари (tasks_tables, works_tables, booking).
Задача/работа, слинкованная с живой бронью (booking.nexus_task_id /
arcana_work_id), в выдачу не попадает — бронь уже представляет ту же встречу.
Вызывать ТОЛЬКО для владельца: названия задач Кай не видны никому, кроме неё.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import sqlalchemy as sa

from arcana.repos.works_tables import work_status, works
from core.booking.busy import _as_utc
from core.booking.tables import BLOCKING_BOOKING_STATUSES, booking
from nexus.repos.tasks_tables import task_status, tasks

MSK = timezone(timedelta(hours=3))
_DONE_CODES = ("Done", "Archived")


@dataclass
class AgendaItem:
    day: date                  # локальный (МСК) день
    kind: str                  # 'task' | 'booking' | 'work'
    title: str
    start: Optional[datetime]  # tz-aware UTC; None не бывает, кроме all_day
    end: Optional[datetime] = None
    all_day: bool = False


def _bounds(day_from: date, day_to: date):
    s = datetime(day_from.year, day_from.month, day_from.day, tzinfo=MSK).astimezone(timezone.utc)
    e = datetime(day_to.year, day_to.month, day_to.day, tzinfo=MSK).astimezone(timezone.utc) + timedelta(days=1)
    return s, e


def _agenda_sync(engine, user_id: str, day_from: date, day_to: date) -> List[AgendaItem]:
    start, end = _bounds(day_from, day_to)
    out: List[AgendaItem] = []

    live = booking.c.status.in_(BLOCKING_BOOKING_STATUSES)
    linked_tasks = sa.select(booking.c.nexus_task_id).where(live).where(booking.c.nexus_task_id.isnot(None))
    linked_works = sa.select(booking.c.arcana_work_id).where(live).where(booking.c.arcana_work_id.isnot(None))
    owner = lambda col: col.in_([user_id, ""])  # noqa: E731

    with engine.connect() as conn:
        done = sa.select(task_status.c.id).where(task_status.c.code.in_(_DONE_CODES))
        tq = (
            sa.select(tasks.c.title, tasks.c.deadline, tasks.c.duration_min, tasks.c.deadline_all_day)
            .where(tasks.c.status_id.notin_(done))
            .where(tasks.c.deadline >= start).where(tasks.c.deadline < end)
            .where(sa.cast(tasks.c.id, sa.Text).notin_(linked_tasks))
        )
        if user_id:
            tq = tq.where(owner(tasks.c.user_id))
        for r in conn.execute(tq):
            d = _as_utc(r.deadline)
            if d is None:
                continue
            all_day = bool(getattr(r, "deadline_all_day", False))
            e = d + timedelta(minutes=r.duration_min) if (r.duration_min and not all_day) else None
            out.append(AgendaItem(d.astimezone(MSK).date(), "task", r.title or "Задача", d, e, all_day))

        wdone = sa.select(work_status.c.id).where(work_status.c.code.in_(_DONE_CODES))
        wq = (
            sa.select(works.c.title, works.c.scheduled_at, works.c.duration_min)
            .where(works.c.scheduled_at >= start).where(works.c.scheduled_at < end)
            .where(works.c.status_id.notin_(wdone))
            .where(sa.cast(works.c.id, sa.Text).notin_(linked_works))
        )
        if user_id:
            wq = wq.where(owner(works.c.user_id))
        for r in conn.execute(wq):
            s = _as_utc(r.scheduled_at)
            if s is None:
                continue
            e = s + timedelta(minutes=r.duration_min) if r.duration_min else None
            out.append(AgendaItem(s.astimezone(MSK).date(), "work", r.title or "Работа", s, e))

        bq = (
            sa.select(booking.c.start_at, booking.c.end_at, booking.c.requester_name, booking.c.note)
            .where(live)
            .where(booking.c.start_at >= start).where(booking.c.start_at < end)
        )
        if user_id:
            bq = bq.where(owner(booking.c.user_id))
        for r in conn.execute(bq):
            s, e = _as_utc(r.start_at), _as_utc(r.end_at)
            if s is None:
                continue
            title = (r.note or "").strip() or (r.requester_name or "").strip() or "Встреча"
            out.append(AgendaItem(s.astimezone(MSK).date(), "booking", title, s, e))

    # внутри дня: «весь день» первыми, дальше по времени
    out.sort(key=lambda i: (i.day, not i.all_day, i.start or datetime.min.replace(tzinfo=timezone.utc)))
    return out


async def day_agenda(user_id: str, day_from: date, day_to: date, *, engine=None) -> List[AgendaItem]:
    """Задачи (дедлайн, не Done/Archived) + брони (pending/confirmed) + работы
    (scheduled_at) за [day_from, day_to] по МСК, без дублей слинкованных с бронью."""
    if engine is None:
        from core.db import get_engine
        engine = get_engine()
    return await asyncio.to_thread(_agenda_sync, engine, user_id, day_from, day_to)
