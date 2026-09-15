"""tests/test_booking_busy.py — core.booking.busy free/busy aggregator (#23 / ADR-0026).

In-memory SQLite (StaticPool), raw CREATE TABLE, engine injected.
Covers: task deadline → 1h busy; reminder-only task → not busy;
Done task → skipped; Arcana work scheduled_at → busy; confirmed booking
uses real start/end; pending booking still holds; manual block; window
overlap filter; merge_intervals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.booking.busy import busy_intervals, merge_intervals

UTC = timezone.utc
T0 = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)   # Monday 09:00


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
        c.execute(sa.text(
            "CREATE TABLE work_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, "
            "emoji TEXT, label TEXT NOT NULL DEFAULT '', sort INTEGER DEFAULT 0)"))
        for code in ("Not started", "In progress", "Done", "Archived"):
            c.execute(sa.text("INSERT INTO task_status (code) VALUES (:c)"), {"c": code})
            c.execute(sa.text("INSERT INTO work_status (code, label) VALUES (:c, :c)"), {"c": code})
        c.execute(sa.text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, reminder TEXT, status_id INTEGER, duration_min INTEGER, user_id TEXT DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, scheduled_at TEXT, status_id INTEGER, duration_min INTEGER, user_id TEXT DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE booking (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "context TEXT, start_at TEXT NOT NULL, end_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "requester_name TEXT DEFAULT '', note TEXT DEFAULT '', token TEXT NOT NULL DEFAULT '', nexus_task_id TEXT)"))
        c.execute(sa.text(
            "CREATE TABLE booking_block (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "start_at TEXT NOT NULL, end_at TEXT NOT NULL, reason TEXT DEFAULT '')"))
    return eng


def _status_id(eng, table, code):
    with eng.connect() as c:
        return c.execute(sa.text(f"SELECT id FROM {table} WHERE code = :c"), {"c": code}).scalar()


async def _run(eng, start=T0, end=T0 + timedelta(days=1), user="u1"):
    return await busy_intervals(user, start, end, engine=eng)


@pytest.mark.asyncio
async def test_task_deadline_becomes_one_hour_busy():
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
                          "('сдать отчёт', :d, :s, 'u1')"), {"d": _iso(T0 + timedelta(hours=5)), "s": ns})
    res = await _run(eng)
    assert len(res) == 1
    assert res[0].source == "task"
    assert res[0].start == T0 + timedelta(hours=5)
    assert res[0].end == T0 + timedelta(hours=6)


@pytest.mark.asyncio
async def test_reminder_only_task_is_not_busy():
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, reminder, status_id, user_id) VALUES "
                          "('полить цветы', :r, :s, 'u1')"), {"r": _iso(T0 + timedelta(hours=3)), "s": ns})
    assert await _run(eng) == []


@pytest.mark.asyncio
async def test_done_task_skipped():
    eng = _make_engine()
    done = _status_id(eng, "task_status", "Done")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
                          "('готово', :d, :s, 'u1')"), {"d": _iso(T0 + timedelta(hours=2)), "s": done})
    assert await _run(eng) == []


@pytest.mark.asyncio
async def test_arcana_work_scheduled_at_is_busy():
    eng = _make_engine()
    ns = _status_id(eng, "work_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO works (title, scheduled_at, status_id, user_id) VALUES "
                          "('расклад Ане', :d, :s, 'u1')"), {"d": _iso(T0 + timedelta(hours=8)), "s": ns})
    res = await _run(eng)
    assert len(res) == 1 and res[0].source == "work"
    assert res[0].end - res[0].start == timedelta(hours=1)


@pytest.mark.asyncio
async def test_pending_and_confirmed_bookings_hold_slot_with_real_span():
    eng = _make_engine()
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status, token) VALUES "
                          "('u1', :s, :e, 'confirmed', 't1')"),
                  {"s": _iso(T0 + timedelta(hours=2)), "e": _iso(T0 + timedelta(hours=4))})
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status, token) VALUES "
                          "('u1', :s, :e, 'pending', 't2')"),
                  {"s": _iso(T0 + timedelta(hours=6)), "e": _iso(T0 + timedelta(hours=7))})
        c.execute(sa.text("INSERT INTO booking (user_id, start_at, end_at, status, token) VALUES "
                          "('u1', :s, :e, 'declined', 't3')"),
                  {"s": _iso(T0 + timedelta(hours=10)), "e": _iso(T0 + timedelta(hours=11))})
    res = await _run(eng)
    assert [r.source for r in res] == ["booking", "booking"]
    assert res[0].end - res[0].start == timedelta(hours=2)


@pytest.mark.asyncio
async def test_booking_label_combines_requester_name_and_topic():
    """Регрессия: label показывал ТОЛЬКО имя забронировавшего ("Никита"),
    без темы встречи (booking.note, "зачем забронировали") — Кай хотела
    видеть и то, и другое."""
    eng = _make_engine()
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO booking (user_id, start_at, end_at, status, token, requester_name, note) "
            "VALUES ('u1', :s, :e, 'confirmed', 't1', 'Никита', 'Секс в Актобе')"),
            {"s": _iso(T0 + timedelta(hours=2)), "e": _iso(T0 + timedelta(hours=4))})
    res = await _run(eng)
    assert res[0].label == "Никита · Секс в Актобе"


@pytest.mark.asyncio
async def test_booking_label_falls_back_to_whichever_field_is_present():
    eng = _make_engine()
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO booking (user_id, start_at, end_at, status, token, requester_name, note) "
            "VALUES ('u1', :s, :e, 'confirmed', 't1', 'Никита', '')"),
            {"s": _iso(T0 + timedelta(hours=2)), "e": _iso(T0 + timedelta(hours=4))})
    res = await _run(eng)
    assert res[0].label == "Никита"


@pytest.mark.asyncio
async def test_manual_block():
    eng = _make_engine()
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO booking_block (user_id, start_at, end_at, reason) VALUES "
                          "('u1', :s, :e, 'отпуск')"),
                  {"s": _iso(T0), "e": _iso(T0 + timedelta(days=1))})
    res = await _run(eng)
    assert len(res) == 1 and res[0].source == "block" and res[0].label == "отпуск"


@pytest.mark.asyncio
async def test_events_outside_window_excluded():
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
                          "('позже', :d, :s, 'u1')"), {"d": _iso(T0 + timedelta(days=3)), "s": ns})
    assert await _run(eng, end=T0 + timedelta(days=1)) == []


@pytest.mark.asyncio
async def test_other_users_tasks_excluded():
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
                          "('чужое', :d, :s, 'u2')"), {"d": _iso(T0 + timedelta(hours=1)), "s": ns})
    assert await _run(eng, user="u1") == []


@pytest.mark.asyncio
async def test_task_duration_min_overrides_default_hour():
    """#241: Кай выставила длительность — busy-интервал берёт её, не дефолтный час."""
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, duration_min, user_id) VALUES "
                          "('встреча с Мишаней', :d, :s, 120, 'u1')"),
                  {"d": _iso(T0 + timedelta(hours=5)), "s": ns})
    res = await _run(eng)
    assert len(res) == 1
    assert res[0].end - res[0].start == timedelta(hours=2)


@pytest.mark.asyncio
async def test_work_duration_min_overrides_default_hour():
    eng = _make_engine()
    ns = _status_id(eng, "work_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO works (title, scheduled_at, status_id, duration_min, user_id) VALUES "
                          "('расклад Ане', :d, :s, 30, 'u1')"),
                  {"d": _iso(T0 + timedelta(hours=8)), "s": ns})
    res = await _run(eng)
    assert len(res) == 1
    assert res[0].end - res[0].start == timedelta(minutes=30)


@pytest.mark.asyncio
async def test_task_without_duration_min_still_defaults_to_hour():
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
                          "('без длительности', :d, :s, 'u1')"),
                  {"d": _iso(T0 + timedelta(hours=5)), "s": ns})
    res = await _run(eng)
    assert res[0].end - res[0].start == timedelta(hours=1)


@pytest.mark.asyncio
async def test_linked_task_excluded_avoids_double_counting_booking():
    """Регрессия: подтверждённая бронь на 4ч создаёт линкованную Nexus-задачу
    (core/booking/linkage.py) — если задача проставлена как "task"-источник
    ЕЩЁ и отдельно, получаются ДВА блока на одну реальную встречу (короткий
    "task" по старой/неверной длительности + правильный "booking"). Задача,
    на которую ссылается booking.nexus_task_id, должна попадать в busy
    ТОЛЬКО через booking-источник."""
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        row = c.execute(sa.text(
            "INSERT INTO tasks (title, deadline, status_id, user_id, duration_min) VALUES "
            "('встреча с Никитой', :d, :s, 'u1', 60) RETURNING id"),
            {"d": _iso(T0 + timedelta(hours=4)), "s": ns}).fetchone()
        task_id = row[0]
        c.execute(sa.text(
            "INSERT INTO booking (user_id, start_at, end_at, status, token, nexus_task_id) VALUES "
            "('u1', :s, :e, 'confirmed', 't-nikita', :tid)"),
            {"s": _iso(T0 + timedelta(hours=4)), "e": _iso(T0 + timedelta(hours=8)),
             "tid": str(task_id)})
    res = await _run(eng)
    assert len(res) == 1
    assert res[0].source == "booking"
    assert res[0].end - res[0].start == timedelta(hours=4)


@pytest.mark.asyncio
async def test_unlinked_task_still_shows_as_task_source():
    """Убедиться, что исключение задевает ТОЛЬКО задачи из активных броней —
    обычная (не привязанная) задача по-прежнему считается занятостью."""
    eng = _make_engine()
    ns = _status_id(eng, "task_status", "Not started")
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO tasks (title, deadline, status_id, user_id) VALUES "
            "('обычная задача', :d, :s, 'u1')"), {"d": _iso(T0 + timedelta(hours=3)), "s": ns})
    res = await _run(eng)
    assert len(res) == 1
    assert res[0].source == "task"


def test_merge_intervals():
    from core.booking.busy import BusyInterval
    ivs = [
        BusyInterval(T0, T0 + timedelta(hours=1), "task"),
        BusyInterval(T0 + timedelta(minutes=30), T0 + timedelta(hours=2), "block"),
        BusyInterval(T0 + timedelta(hours=5), T0 + timedelta(hours=6), "booking"),
    ]
    merged = merge_intervals(ivs)
    assert merged == [
        (T0, T0 + timedelta(hours=2)),
        (T0 + timedelta(hours=5), T0 + timedelta(hours=6)),
    ]
