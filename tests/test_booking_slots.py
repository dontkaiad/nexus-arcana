"""tests/test_booking_slots.py — core.booking.slots.free_slots (#23 B2 / ADR-0026).

In-memory SQLite; availability windows → slots, minus busy, minus notice/advance.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.booking.slots import free_slots

UTC = timezone.utc
# Mon 2026-09-14; "now" well before the test week so min_notice is satisfied
NOW = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def _make_engine():
    eng = sa.create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
        c.execute(sa.text(
            "CREATE TABLE work_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, "
            "emoji TEXT, label TEXT NOT NULL DEFAULT '', sort INTEGER DEFAULT 0)"))
        for code in ("Not started", "Done", "Archived"):
            c.execute(sa.text("INSERT INTO task_status (code) VALUES (:c)"), {"c": code})
            c.execute(sa.text("INSERT INTO work_status (code, label) VALUES (:c, :c)"), {"c": code})
        c.execute(sa.text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, reminder TEXT, status_id INTEGER, user_id TEXT DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, scheduled_at TEXT, status_id INTEGER, user_id TEXT DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE booking (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "context TEXT, start_at TEXT NOT NULL, end_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "requester_name TEXT DEFAULT '', token TEXT NOT NULL DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE booking_block (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "start_at TEXT NOT NULL, end_at TEXT NOT NULL, reason TEXT DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE booking_availability (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "context TEXT NOT NULL, weekday INTEGER NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, "
            "tz TEXT NOT NULL DEFAULT 'Europe/Moscow', slot_minutes INTEGER NOT NULL DEFAULT 60, "
            "min_notice_hours INTEGER NOT NULL DEFAULT 12, max_advance_days INTEGER NOT NULL DEFAULT 60, "
            "buffer_before_min INTEGER NOT NULL DEFAULT 0, buffer_after_min INTEGER NOT NULL DEFAULT 0, "
            "active INTEGER NOT NULL DEFAULT 1)"))
    return eng


def _add_window(eng, **kw):
    kw.setdefault("user_id", "u1"); kw.setdefault("context", "friends")
    kw.setdefault("tz", "+03:00"); kw.setdefault("slot_minutes", 60)
    kw.setdefault("min_notice_hours", 12); kw.setdefault("max_advance_days", 60)
    kw.setdefault("buffer_before_min", 0); kw.setdefault("buffer_after_min", 0)
    kw.setdefault("active", 1)
    cols = ", ".join(kw); ph = ", ".join(f":{k}" for k in kw)
    with eng.begin() as c:
        c.execute(sa.text(f"INSERT INTO booking_availability ({cols}) VALUES ({ph})"), kw)


async def _run(eng, d_from=date(2026, 9, 14), d_to=date(2026, 9, 14), ctx="friends"):
    return await free_slots("u1", ctx, day_from=d_from, day_to=d_to, now=NOW, engine=eng)


@pytest.mark.asyncio
async def test_window_expands_to_hourly_slots():
    eng = _make_engine()
    # Monday (weekday 0) 14:00–17:00 +03:00 → 11:00,12:00,13:00 UTC
    _add_window(eng, weekday=0, start_time="14:00", end_time="17:00")
    slots = await _run(eng)
    starts = [s.start.hour for s in slots]
    assert starts == [11, 12, 13]
    assert all(s.end - s.start == timedelta(hours=1) for s in slots)


@pytest.mark.asyncio
async def test_no_window_that_weekday():
    eng = _make_engine()
    _add_window(eng, weekday=2, start_time="14:00", end_time="17:00")  # Wed only
    assert await _run(eng) == []  # asking for Monday


@pytest.mark.asyncio
async def test_busy_task_removes_overlapping_slot():
    eng = _make_engine()
    _add_window(eng, weekday=0, start_time="14:00", end_time="17:00")
    ns = None
    with eng.connect() as c:
        ns = c.execute(sa.text("SELECT id FROM task_status WHERE code='Not started'")).scalar()
    with eng.begin() as c:
        # deadline 15:00 +03 = 12:00 UTC → busy 12:00–13:00 UTC
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
                          "('созвон', :d, :s, 'u1')"), {"d": _iso(datetime(2026, 9, 14, 12, 0, tzinfo=UTC)), "s": ns})
    slots = await _run(eng)
    assert [s.start.hour for s in slots] == [11, 13]


@pytest.mark.asyncio
async def test_min_notice_cuts_near_slots():
    eng = _make_engine()
    _add_window(eng, weekday=0, start_time="09:00", end_time="23:00", min_notice_hours=12)
    # now = 2026-09-14 06:00 UTC → notice cut 18:00 UTC → only slots >= 18:00 UTC
    slots = await free_slots("u1", "friends", day_from=date(2026, 9, 14), day_to=date(2026, 9, 14),
                             now=datetime(2026, 9, 14, 6, 0, tzinfo=UTC), engine=eng)
    assert slots and min(s.start.hour for s in slots) >= 18


@pytest.mark.asyncio
async def test_max_advance_cuts_far_slots():
    eng = _make_engine()
    _add_window(eng, weekday=0, start_time="14:00", end_time="17:00", max_advance_days=2)
    # NOW = 2026-09-10; advance cut = 2026-09-12 → the 2026-09-14 window is out
    assert await _run(eng) == []


@pytest.mark.asyncio
async def test_meeting_must_fit_before_window_closes():
    eng = _make_engine()
    _add_window(eng, weekday=0, start_time="14:00", end_time="15:30", slot_minutes=60)
    # 14:00 fits (→15:00), 15:00 would end 16:00 > 15:30 → only one slot
    slots = await _run(eng)
    assert len(slots) == 1 and slots[0].start.hour == 11  # 14:00 +03


@pytest.mark.asyncio
async def test_buffer_widens_busy_check():
    eng = _make_engine()
    # slots 11:00, 12:00, 13:00 UTC. block 12:40–12:50 UTC.
    # 12:00 slot overlaps directly. 11:00 slot (ends 12:00) only overlaps once
    # its +30m buffer_after (→12:30) ... still not 12:40. Use buffer 45m.
    _add_window(eng, weekday=0, start_time="14:00", end_time="17:00", buffer_after_min=45)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO booking_block (user_id, start_at, end_at, reason) VALUES "
                          "('u1', :s, :e, 'x')"),
                  {"s": _iso(datetime(2026, 9, 14, 12, 40, tzinfo=UTC)),
                   "e": _iso(datetime(2026, 9, 14, 12, 50, tzinfo=UTC))})
    slots = await _run(eng)
    # 11:00 (ends 12:00, +45m buffer → 12:45) hits the 12:40 block → killed
    # 12:00 hits directly → killed.  13:00 (starts 13:00, buffer_before 0) → free
    assert [s.start.hour for s in slots] == [13]
