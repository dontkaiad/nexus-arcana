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
from typing import Optional

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

        # #239: заголовок = ПОВОД встречи (обязателен для друзей — Кай
        # хочет видеть "шашлыки", а не безликое "Встреча: Мишаня"), имя —
        # в заметке вместе с технической меткой брони.
        purpose = (b.note or "").strip()
        title = f"☕ {purpose}" if purpose else f"☕ Встреча: {b.requester_name}"
        task_note = f"с {b.requester_name} · бронь #{b.id} · {b.hours or 1:g} ч · {b.source}"
        # #249: длительность брони → duration_min задачи (busy-калькулятор,
        # core/booking/busy.py, брал раньше жёсткий 1ч на любую задачу — #241).
        duration_min = int(round(b.hours * 60)) if b.hours else None

        with eng.begin() as conn:
            sid = conn.execute(
                sa.select(task_status.c.id).where(task_status.c.code == "Not started")
            ).scalar()
            row = conn.execute(
                tasks.insert().values(
                    title=title,
                    status_id=sid,
                    deadline=b.start_at,
                    user_id=uid or "",
                    note=task_note,
                    duration_min=duration_min,
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


def _retime_sync(eng, b: Booking) -> None:
    # #249: перенос брони может поменять и длительность (RescheduleBody.hours)
    # — синкаем duration_min вместе с временем, иначе Nexus-задача врёт про
    # занятость дольше/короче реального нового окна.
    duration_min = int(round(b.hours * 60)) if b.hours else None
    with eng.begin() as conn:
        if b.nexus_task_id:
            from nexus.repos.tasks_tables import tasks
            conn.execute(
                tasks.update().where(tasks.c.id == int(b.nexus_task_id))
                .values(deadline=b.start_at, duration_min=duration_min)
            )
        if b.arcana_work_id:
            from arcana.repos.works_tables import works
            conn.execute(
                works.update().where(works.c.id == int(b.arcana_work_id)).values(scheduled_at=b.start_at)
            )


async def update_linked_time(b: Booking, *, engine=None) -> None:
    """#220 (B7): admin перенёс бронь на другое время — синкнуть дедлайн
    Nexus-задачи / scheduled_at Работы на новое b.start_at. Never raises."""
    if not (b.nexus_task_id or b.arcana_work_id):
        return
    try:
        await asyncio.to_thread(_retime_sync, engine or _engine(), b)
        logger.info("booking #%s retimed linked task/work → %s", b.id, b.start_at)
    except Exception as e:  # noqa: BLE001
        logger.warning("update_linked_time #%s failed: %s", b.id, e)


# ── booking_block (#249) — тот же паттерн, что confirmed booking → task ──────
#
# "я ставлю в букинг что я занята весь день ... у меня нет задачи в нексусе
# на это — неудобно, я же на нексус ориентируюсь". Ручной блок в букинге уже
# блокирует слоты (core/booking/busy.py source='block'), но был невидим в
# Nexus. Здесь — только создание/архивация линкованной задачи; сам блок
# CRUD (booking_block-строка) остаётся в core/booking/repo.py.

_MAX_TASK_DURATION_MIN = 24 * 60  # #249: дольше суток — просто не проставляем


def _link_block_sync(eng, block: dict) -> Optional[str]:
    from nexus.repos.tasks_tables import task_status, tasks

    start_at, end_at = block["start_at"], block["end_at"]
    reason = (block.get("reason") or "").strip()
    title = f"🚫 {reason}" if reason else "🚫 Занята (букинг)"
    span_days = (end_at.date() - start_at.date()).days
    note = f"букинг-блок #{block['id']}"
    if span_days > 0:
        note += f" · {start_at:%d.%m} – {end_at:%d.%m}"
    duration_min = int((end_at - start_at).total_seconds() // 60)
    if duration_min <= 0 or duration_min > _MAX_TASK_DURATION_MIN:
        duration_min = None

    with eng.begin() as conn:
        sid = conn.execute(
            sa.select(task_status.c.id).where(task_status.c.code == "Not started")
        ).scalar()
        row = conn.execute(
            tasks.insert().values(
                title=title,
                status_id=sid,
                deadline=start_at,
                user_id=block.get("user_id") or "",
                note=note,
                duration_min=duration_min,
            ).returning(tasks.c.id)
        ).first()
    return str(row[0]) if row else None


def _set_block_link_sync(eng, block_id: int, task_id: str) -> None:
    from core.booking.tables import booking_block
    with eng.begin() as conn:
        conn.execute(
            booking_block.update().where(booking_block.c.id == block_id)
            .values(nexus_task_id=task_id)
        )


async def link_block(block: dict, *, engine=None) -> dict:
    """Create the linked Nexus task for a just-created booking_block, write its
    id back onto the block row. Never raises — a failed link must not break
    the block itself (booking still blocks slots either way)."""
    eng = engine or _engine()
    try:
        task_id = await asyncio.to_thread(_link_block_sync, eng, block)
        if task_id:
            await asyncio.to_thread(_set_block_link_sync, eng, block["id"], task_id)
            block = dict(block, nexus_task_id=task_id)
            logger.info("booking_block #%s linked → nexus_task_id=%s", block["id"], task_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("link_block #%s failed: %s", block.get("id"), e)
    return block


def _unlink_block_sync(eng, task_id: str) -> None:
    from nexus.repos.tasks_tables import task_status, tasks
    with eng.begin() as conn:
        aid = conn.execute(
            sa.select(task_status.c.id).where(task_status.c.code == "Archived")
        ).scalar()
        conn.execute(tasks.update().where(tasks.c.id == int(task_id)).values(status_id=aid))


async def unlink_block(nexus_task_id: Optional[str], *, engine=None) -> None:
    """Archive the linked task when a booking_block is removed. Never raises."""
    if not nexus_task_id:
        return
    try:
        await asyncio.to_thread(_unlink_block_sync, engine or _engine(), nexus_task_id)
        logger.info("booking_block task #%s archived (block removed)", nexus_task_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("unlink_block task #%s failed: %s", nexus_task_id, e)
