"""core/booking/linkage.py — confirmed booking → Nexus task / 🔮 Work (#23 B6, ADR-0026 §3).

Direct SQLAlchemy-Core writes against the `tasks` / `works` tables. Zarya's
narrow image ships those table defs (`nexus/repos/tasks_tables`,
`arcana/repos/works_tables`) but NOT the full repos, so we insert here rather
than call `pg_tasks_repo` / `pg_works_repo`.

  friends → `tasks` row, deadline = meeting start (shows in «Мой день», no
            reminder — Zarya owns the reminders)
  arcana  → `works` row, status 'scheduled' (🗓 Запланировано), scheduled_at =
            meeting start (feeds free/busy + open-work lists)

The created id is written back onto `booking.nexus_task_id` / `arcana_work_id`
by `link_booking`. `unlink_booking` archives them on cancellation.
"""
from __future__ import annotations

import asyncio
import logging

import sqlalchemy as sa

from core.booking.repo import Booking, _set_link_sync

logger = logging.getLogger("booking.linkage")


def _engine():
    from core.db import get_engine
    return get_engine()


def _link_sync(eng, b: Booking):
    uid = b.user_id or None
    if b.context == "friends":
        from nexus.repos.tasks_tables import task_status, tasks

        with eng.begin() as conn:
            sid = conn.execute(
                sa.select(task_status.c.id).where(task_status.c.code == "Not started")
            ).scalar()
            row = conn.execute(
                tasks.insert().values(
                    title=f"☕ Встреча: {b.requester_name}",
                    status_id=sid,
                    deadline=b.start_at,
                    user_id=uid or "",
                    note=f"бронь #{b.id} · {b.hours or 1:g} ч · {b.source}",
                ).returning(tasks.c.id)
            ).first()
        return ("nexus_task_id", str(row[0]))

    from arcana.repos.works_tables import work_status, works

    with eng.begin() as conn:
        sid = conn.execute(
            sa.select(work_status.c.id).where(work_status.c.code == "scheduled")
        ).scalar()
        if sid is None:  # migration not applied yet → fall back to 'open'
            sid = conn.execute(
                sa.select(work_status.c.id).where(work_status.c.code == "open")
            ).scalar()
        row = conn.execute(
            works.insert().values(
                title=f"🔮 {b.requester_name}",
                status_id=sid,
                scheduled_at=b.start_at,
                category="🃏 Расклад",
                user_id=uid,
            ).returning(works.c.id)
        ).first()
    return ("arcana_work_id", str(row[0]))


async def link_booking(b: Booking, *, engine=None) -> Booking:
    """Create the linked task/work for a just-confirmed booking. Skips if already
    linked. Never raises — a failed link must not break confirmation."""
    if b.nexus_task_id or b.arcana_work_id:
        return b
    eng = engine or _engine()
    try:
        field, new_id = await asyncio.to_thread(_link_sync, eng, b)
        updated = await asyncio.to_thread(_set_link_sync, eng, b.id, **{field: new_id})
        logger.info("booking #%s linked → %s=%s", b.id, field, new_id)
        return updated or b
    except Exception as e:  # noqa: BLE001
        logger.warning("link_booking #%s failed: %s", b.id, e)
        return b


def _unlink_sync(eng, b: Booking) -> None:
    with eng.begin() as conn:
        if b.nexus_task_id:
            from nexus.repos.tasks_tables import task_status, tasks

            aid = conn.execute(
                sa.select(task_status.c.id).where(task_status.c.code == "Archived")
            ).scalar()
            conn.execute(
                tasks.update().where(tasks.c.id == int(b.nexus_task_id)).values(status_id=aid)
            )
        if b.arcana_work_id:
            from arcana.repos.works_tables import work_status, works

            aid = conn.execute(
                sa.select(work_status.c.id).where(work_status.c.code == "archived")
            ).scalar()
            conn.execute(
                works.update().where(works.c.id == int(b.arcana_work_id)).values(status_id=aid)
            )


async def unlink_booking(b: Booking, *, engine=None) -> None:
    """Archive the linked task/work when a booking is cancelled. Never raises."""
    if not (b.nexus_task_id or b.arcana_work_id):
        return
    try:
        await asyncio.to_thread(_unlink_sync, engine or _engine(), b)
        logger.info("booking #%s unlinked (archived task/work)", b.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("unlink_booking #%s failed: %s", b.id, e)
