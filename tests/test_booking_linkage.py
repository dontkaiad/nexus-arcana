"""tests/test_booking_linkage.py — confirmed booking → Nexus task / 🔮 Work (#23 B6).

In-memory SQLite; real table defs; verifies link creates the row, writes the id
back on the booking, and unlink archives it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.booking.linkage import link_booking, unlink_booking, update_linked_time
from core.booking.repo import create_booking, reschedule_booking

UTC = timezone.utc
START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

# raw CREATE (sqlite can't run the pg `now()` server-defaults) — mirror
# core/booking/tables.py:booking column-for-column so `.returning(booking)` works.
_BOOKING_DDL = """
CREATE TABLE booking (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT '',
  context TEXT NOT NULL, meeting_type_id INTEGER, requester_tg_id INTEGER,
  requester_name TEXT NOT NULL DEFAULT '', requester_contact TEXT NOT NULL DEFAULT '',
  start_at TEXT NOT NULL, end_at TEXT NOT NULL, hours NUMERIC,
  status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'web', hold_expires_at TEXT,
  nexus_task_id TEXT, arcana_work_id TEXT, token TEXT NOT NULL DEFAULT '',
  created_at TEXT, decided_at TEXT
)
"""


def _engine():
    eng = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with eng.begin() as c:
        c.execute(sa.text(_BOOKING_DDL))
        c.execute(sa.text(
            "CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
        c.execute(sa.text(
            "CREATE TABLE work_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, "
            "emoji TEXT, label TEXT NOT NULL DEFAULT '', sort INTEGER DEFAULT 0)"))
        for code in ("Not started", "In progress", "Done", "Archived"):
            c.execute(sa.text("INSERT INTO task_status (code) VALUES (:c)"), {"c": code})
        for code, label in (("open", "Открыто"), ("done", "Готово"),
                            ("archived", "Архив"), ("scheduled", "Запланировано")):
            c.execute(sa.text("INSERT INTO work_status (code, label) VALUES (:c, :l)"),
                      {"c": code, "l": label})
        c.execute(sa.text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, reminder TEXT, status_id INTEGER, note TEXT, user_id TEXT DEFAULT '', "
            "duration_min INTEGER)"))
        c.execute(sa.text(
            "CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, scheduled_at TEXT, category TEXT, status_id INTEGER, user_id TEXT DEFAULT '')"))
    return eng


async def _booking(eng, ctx):
    return await create_booking(
        user_id="u1", context=ctx, start_at=START, end_at=START + timedelta(hours=2),
        status="confirmed", hours=2, requester_name="Марина", requester_tg_id=5,
        source="tg_dm", engine=eng,
    )


@pytest.mark.asyncio
async def test_friends_booking_creates_task():
    eng = _engine()
    b = await _booking(eng, "friends")
    linked = await link_booking(b, engine=eng)
    assert linked.nexus_task_id and not linked.arcana_work_id
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT title, deadline, reminder FROM tasks WHERE id = :i"),
            {"i": int(linked.nexus_task_id)}).first()
    assert row.title == "☕ Встреча: Марина"
    assert str(row.deadline).startswith("2026-10-01 12:00")
    assert row.reminder is None  # Zarya owns reminders, not the task


@pytest.mark.asyncio
async def test_friends_booking_title_uses_purpose_when_present():
    """#239: "не просто встреча с тем-то, а НА ЧТО" — заголовок задачи =
    повод встречи (booking.note), имя уходит в заметку задачи."""
    eng = _engine()
    b = await create_booking(
        user_id="u1", context="friends", start_at=START, end_at=START + timedelta(hours=2),
        status="confirmed", hours=2, requester_name="Мишаня", requester_tg_id=5,
        note="шашлыки в Токсово", source="tg_dm", engine=eng,
    )
    linked = await link_booking(b, engine=eng)
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT title, note FROM tasks WHERE id = :i"),
            {"i": int(linked.nexus_task_id)}).first()
    assert row.title == "☕ шашлыки в Токсово"
    assert "Мишаня" in row.note


@pytest.mark.asyncio
async def test_arcana_booking_creates_scheduled_work():
    eng = _engine()
    b = await _booking(eng, "arcana")
    linked = await link_booking(b, engine=eng)
    assert linked.arcana_work_id and not linked.nexus_task_id
    with eng.connect() as c:
        w = c.execute(sa.text(
            "SELECT scheduled_at, status_id, category FROM works WHERE id = :i"),
            {"i": int(linked.arcana_work_id)}).first()
        sid = c.execute(sa.text("SELECT code FROM work_status WHERE id = :i"),
                        {"i": w.status_id}).scalar()
    assert str(w.scheduled_at).startswith("2026-10-01 12:00")
    assert w.category == "🃏 Расклад"
    assert sid == "scheduled"


@pytest.mark.asyncio
async def test_link_is_idempotent():
    eng = _engine()
    b = await _booking(eng, "friends")
    first = await link_booking(b, engine=eng)
    again = await link_booking(first, engine=eng)
    assert again.nexus_task_id == first.nexus_task_id
    with eng.connect() as c:
        n = c.execute(sa.text("SELECT count(*) FROM tasks")).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_unlink_archives_task():
    eng = _engine()
    b = await link_booking(await _booking(eng, "friends"), engine=eng)
    await unlink_booking(b, engine=eng)
    with eng.connect() as c:
        sid = c.execute(sa.text("SELECT status_id FROM tasks WHERE id = :i"),
                        {"i": int(b.nexus_task_id)}).scalar()
        code = c.execute(sa.text("SELECT code FROM task_status WHERE id = :i"), {"i": sid}).scalar()
    assert code == "Archived"


@pytest.mark.asyncio
async def test_unlink_noop_when_unlinked():
    eng = _engine()
    b = await _booking(eng, "friends")
    await unlink_booking(b, engine=eng)  # must not raise


# ── #220 (B7): reschedule → синк дедлайна задачи / scheduled_at Работы ──────

@pytest.mark.asyncio
async def test_reschedule_friends_updates_task_deadline():
    eng = _engine()
    b = await link_booking(await _booking(eng, "friends"), engine=eng)
    new_start = START + timedelta(days=3)
    moved = await reschedule_booking(b.id, new_start, new_start + timedelta(hours=1), engine=eng)
    await update_linked_time(moved, engine=eng)
    with eng.connect() as c:
        row = c.execute(sa.text("SELECT deadline FROM tasks WHERE id = :i"),
                        {"i": int(b.nexus_task_id)}).first()
    assert str(row.deadline).startswith("2026-10-04 12:00")


@pytest.mark.asyncio
async def test_reschedule_arcana_updates_work_scheduled_at():
    eng = _engine()
    b = await link_booking(await _booking(eng, "arcana"), engine=eng)
    new_start = START + timedelta(days=3)
    moved = await reschedule_booking(b.id, new_start, new_start + timedelta(hours=1), engine=eng)
    await update_linked_time(moved, engine=eng)
    with eng.connect() as c:
        row = c.execute(sa.text("SELECT scheduled_at FROM works WHERE id = :i"),
                        {"i": int(b.arcana_work_id)}).first()
    assert str(row.scheduled_at).startswith("2026-10-04 12:00")


@pytest.mark.asyncio
async def test_update_linked_time_noop_when_unlinked():
    eng = _engine()
    b = await _booking(eng, "friends")
    await update_linked_time(b, engine=eng)  # must not raise, nothing to update
