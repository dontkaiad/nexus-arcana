"""#273: коды статусов works в PG — нижний регистр ('done','archived'), задач Nexus — 'Done'/'Archived'.
Закрытая работа со scheduled_at не должна занимать слот Зари (busy_intervals и agenda)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.booking.agenda import day_agenda
from core.booking.busy import busy_intervals

UTC = timezone.utc
AT = datetime(2026, 11, 10, 16, 45, tzinfo=UTC)
DAY = date(2026, 11, 10)


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def _engine():
    e = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with e.begin() as c:
        c.execute(sa.text("CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
        c.execute(sa.text("CREATE TABLE work_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, "
                          "emoji TEXT, label TEXT NOT NULL DEFAULT '', sort INTEGER DEFAULT 0)"))
        for code in ("Not started", "Done", "Archived"):          # как у задач Nexus
            c.execute(sa.text("INSERT INTO task_status (code) VALUES (:c)"), {"c": code})
        for code in ("open", "scheduled", "done", "archived"):    # как у works в PG
            c.execute(sa.text("INSERT INTO work_status (code, label) VALUES (:c, :c)"), {"c": code})
        c.execute(sa.text("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                          "deadline TEXT, reminder TEXT, status_id INTEGER, duration_min INTEGER, "
                          "user_id TEXT DEFAULT '', deadline_all_day INTEGER NOT NULL DEFAULT 0)"))
        c.execute(sa.text("CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                          "deadline TEXT, scheduled_at TEXT, status_id INTEGER, duration_min INTEGER, "
                          "user_id TEXT DEFAULT '')"))
        c.execute(sa.text("CREATE TABLE booking (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
                          "context TEXT, start_at TEXT NOT NULL, end_at TEXT NOT NULL, "
                          "status TEXT NOT NULL DEFAULT 'pending', requester_name TEXT DEFAULT '', "
                          "note TEXT DEFAULT '', token TEXT NOT NULL DEFAULT '', nexus_task_id TEXT, arcana_work_id TEXT)"))
        c.execute(sa.text("CREATE TABLE booking_block (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
                          "start_at TEXT NOT NULL, end_at TEXT NOT NULL, reason TEXT DEFAULT '')"))
    return e


def _sid(e, table, code):
    with e.connect() as c:
        return c.execute(sa.text(f"SELECT id FROM {table} WHERE code=:c"), {"c": code}).scalar()


def _work(e, status):
    with e.begin() as c:
        c.execute(sa.text("INSERT INTO works (title, scheduled_at, status_id, duration_min, user_id) "
                          "VALUES ('w', :d, :s, 60, 'u1')"),
                  {"d": _iso(AT), "s": _sid(e, "work_status", status)})


def _task(e, status):
    with e.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES ('t', :d, :s, 'u1')"),
                  {"d": _iso(AT + timedelta(hours=3)), "s": _sid(e, "task_status", status)})


async def _busy(e):
    return await busy_intervals("u1", AT - timedelta(hours=6), AT + timedelta(hours=12), engine=e)


async def _agenda(e):
    return await day_agenda("u1", DAY, DAY, engine=e)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "archived"])
async def test_closed_work_does_not_block_or_show_in_agenda(status):
    e = _engine()
    _work(e, status)
    assert await _busy(e) == []
    assert [i for i in await _agenda(e) if i.kind == "work"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["open", "scheduled"])
async def test_open_work_blocks_and_shows(status):
    e = _engine()
    _work(e, status)
    res = await _busy(e)
    assert len(res) == 1 and res[0].source == "work"
    assert [i.kind for i in await _agenda(e)] == ["work"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("Done", 0), ("Archived", 0), ("Not started", 1)])
async def test_nexus_task_codes_still_work(status, expected):
    e = _engine()
    _task(e, status)
    assert len(await _busy(e)) == expected
    assert len([i for i in await _agenda(e) if i.kind == "task"]) == expected


@pytest.mark.asyncio
async def test_bookings_unaffected():
    e = _engine()
    with e.begin() as c:
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status) VALUES "
                          "('u1', :s, :e, 'confirmed')"), {"s": _iso(AT), "e": _iso(AT + timedelta(hours=1))})
    res = await _busy(e)
    assert len(res) == 1 and res[0].source == "booking"
