"""#272: ритуал/расклад с явным временем занимает слот Зари БЕЗ изменений busy.py —
писатели ставят scheduled_at = deadline и duration_min = 60."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import BigInteger, Column, Table
from sqlalchemy.pool import StaticPool

import arcana.repos.pg_works_repo as pgw
from arcana.repos.rituals_tables import metadata
from arcana.repos.works_tables import (
    work_day_of_week, work_priority, work_repeat, work_status, works,
)
from core.booking.busy import busy_intervals

UTC = timezone.utc
START = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
END = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
# «сегодня 19:45 МСК» = 16:45 UTC
RITUAL_AT = datetime(2026, 11, 1, 16, 45, tzinfo=UTC)


@pytest.fixture
def eng(monkeypatch):
    stub = Table("clients", metadata, Column("id", BigInteger, primary_key=True))
    e = sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @sa.event.listens_for(e, "connect")
    def _now(dbapi_conn, _rec):  # sqlite не знает now() (server_default в works)
        dbapi_conn.create_function("now", 0, lambda: "2026-11-01 00:00:00.000000")
    try:
        metadata.create_all(e, tables=[work_status, work_priority, work_repeat,
                                       work_day_of_week])
        with e.begin() as c:
            for sid, code in [(1, "open"), (2, "done"), (3, "archived")]:
                c.execute(work_status.insert().values(id=sid, code=code, label=code))
            for pid, code in [(1, "urgent"), (2, "important"), (3, "later")]:
                c.execute(work_priority.insert().values(id=pid, code=code, label=code))
            c.execute(work_repeat.insert().values(id=1, code="none", label="Нет"))
            # works — raw: BigInteger PK в sqlite не автоинкрементится
            c.execute(sa.text(
                "CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "deadline TIMESTAMP, category TEXT, notes TEXT, priority_id INTEGER, status_id INTEGER, "
                "client_id INTEGER, repeat_id INTEGER, day_of_week_id INTEGER, repeat_time TEXT, "
                "reminder TIMESTAMP, scheduled_at TIMESTAMP, duration_min INTEGER, user_id TEXT, "
                "created_at TIMESTAMP, updated_at TIMESTAMP)"))
            c.execute(sa.text("CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
            c.execute(sa.text(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "deadline TEXT, reminder TEXT, status_id INTEGER, duration_min INTEGER, "
                "user_id TEXT DEFAULT '', deadline_all_day INTEGER NOT NULL DEFAULT 0)"))
            c.execute(sa.text(
                "CREATE TABLE booking (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
                "context TEXT, start_at TEXT NOT NULL, end_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
                "requester_name TEXT DEFAULT '', note TEXT DEFAULT '', token TEXT NOT NULL DEFAULT '', nexus_task_id TEXT)"))
            c.execute(sa.text(
                "CREATE TABLE booking_block (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
                "start_at TEXT NOT NULL, end_at TEXT NOT NULL, reason TEXT DEFAULT '')"))
        monkeypatch.setattr(pgw, "get_engine", lambda: e)
        yield e
    finally:
        metadata.remove(stub)


async def _busy(e):
    return await busy_intervals("u1", START, END, engine=e)


async def _create(category, deadline, tz=3):
    return await pgw.PgWorksRepo().create(
        title="w", priority="Срочно", deadline=deadline, category=category,
        user_id="u1", tz_offset=tz,
    )


@pytest.mark.asyncio
async def test_ritual_with_time_blocks_1943_to_2045_msk(eng):
    await _create("✨ Ритуал", RITUAL_AT)
    res = await _busy(eng)
    assert len(res) == 1 and res[0].source == "work"
    assert res[0].start == RITUAL_AT and res[0].end == RITUAL_AT + timedelta(hours=1)
    msk = timezone(timedelta(hours=3))
    assert res[0].start.astimezone(msk).strftime("%H:%M") == "19:45"
    assert res[0].end.astimezone(msk).strftime("%H:%M") == "20:45"


@pytest.mark.asyncio
async def test_session_with_time_blocks_too(eng):
    await _create("🃏 Расклад", RITUAL_AT)
    assert len(await _busy(eng)) == 1


@pytest.mark.asyncio
async def test_ritual_without_time_does_not_block(eng):
    # дата без времени = локальная полночь (00:00 МСК = 21:00 UTC накануне)
    await _create("✨ Ритуал", datetime(2026, 10, 31, 21, 0, tzinfo=UTC))
    assert await _busy(eng) == []


@pytest.mark.asyncio
async def test_other_category_does_not_block(eng):
    await _create("📱 Соцсети", RITUAL_AT)
    assert await _busy(eng) == []


@pytest.mark.asyncio
async def test_done_work_does_not_block(eng):
    wid = await _create("✨ Ритуал", RITUAL_AT)
    assert len(await _busy(eng)) == 1
    await pgw.PgWorksRepo().mark_done(wid)
    assert await _busy(eng) == []


@pytest.mark.asyncio
async def test_archived_work_does_not_block(eng):
    wid = await _create("🃏 Расклад", RITUAL_AT)
    await pgw.PgWorksRepo().set_status(wid, "archived")
    assert await _busy(eng) == []


@pytest.mark.asyncio
async def test_set_props_moves_slot_with_deadline(eng):
    repo = pgw.PgWorksRepo()
    wid = await _create("✨ Ритуал", RITUAL_AT)
    await repo.set_props(wid, tz_offset=3, deadline="2026-11-01 21:00")  # 21:00 МСК = 18:00 UTC
    res = await _busy(eng)
    assert len(res) == 1
    with eng.connect() as c:  # слот двигается вместе с дедлайном (sqlite не хранит пояс → сравниваем колонки)
        row = c.execute(sa.text("SELECT deadline, scheduled_at FROM works")).fetchone()
    assert row.scheduled_at == row.deadline and row.scheduled_at != "2026-11-01 16:45:00.000000"


@pytest.mark.asyncio
async def test_set_deadline_to_plain_date_releases_slot(eng):
    repo = pgw.PgWorksRepo()
    wid = await _create("✨ Ритуал", RITUAL_AT)
    await repo.set_deadline(wid, date(2026, 11, 5))
    assert await _busy(eng) == []


@pytest.mark.asyncio
async def test_booking_scheduled_at_is_not_touched_by_postpone(eng):
    """scheduled_at брони (≠ deadline) — не «наш» слот: перенос дедлайна его не трогает."""
    with eng.begin() as c:
        c.execute(works.insert().values(
            id=50, title="бронь", status_id=1, priority_id=3, user_id="u1",
            deadline=datetime(2026, 11, 3, 10, 0), scheduled_at=RITUAL_AT.replace(tzinfo=None),
            duration_min=90))
    await pgw.PgWorksRepo().set_deadline("50", date(2026, 11, 9))
    res = await _busy(eng)
    assert len(res) == 1 and res[0].end - res[0].start == timedelta(minutes=90)
