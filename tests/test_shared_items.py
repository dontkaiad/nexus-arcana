"""tests/test_shared_items.py — core.shared_items (#242).

"флаг shared на задачах/списках, чтобы бот мог сказать «Кай хочет в кино на
выходных»" — in-memory SQLite, real table shape (tasks + task_status +
nexus_lists). "и подключи колонку к заре" — nexus_lists.shared тоже читается.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.shared_items import shared_items_summary

UTC = timezone.utc


def _engine():
    eng = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE task_status (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL)"))
        for code in ("Not started", "In progress", "Done", "Archived"):
            c.execute(sa.text("INSERT INTO task_status (code) VALUES (:c)"), {"c": code})
        c.execute(sa.text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TEXT, status_id INTEGER, shared BOOLEAN NOT NULL DEFAULT 0, "
            "user_id TEXT DEFAULT '')"))
        c.execute(sa.text(
            "CREATE TABLE nexus_lists (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'not_started', expires_at DATE, "
            "shared BOOLEAN NOT NULL DEFAULT 0, user_id TEXT DEFAULT '')"))
    return eng


def _status_id(eng, code):
    with eng.connect() as c:
        return c.execute(sa.text("SELECT id FROM task_status WHERE code = :c"), {"c": code}).scalar()


def _insert_task(eng, title, deadline, shared, status="Not started", user_id="u1"):
    sid = _status_id(eng, status)
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO tasks (title, deadline, status_id, shared, user_id) VALUES "
            "(:t, :d, :s, :sh, :u)"),
            {"t": title, "d": deadline.isoformat() if deadline else None, "s": sid,
             "sh": shared, "u": user_id})


def _insert_list_item(eng, name, expires_at, shared, status="not_started", user_id="u1"):
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO nexus_lists (name, expires_at, status, shared, user_id) VALUES "
            "(:n, :e, :s, :sh, :u)"),
            {"n": name, "e": expires_at.isoformat() if expires_at else None, "s": status,
             "sh": shared, "u": user_id})


@pytest.mark.asyncio
async def test_no_shared_items_returns_empty_string():
    eng = _engine()
    _insert_task(eng, "приватная задача", None, shared=False)
    assert await shared_items_summary("u1", engine=eng) == ""


@pytest.mark.asyncio
async def test_shared_task_without_deadline_has_no_hint():
    eng = _engine()
    _insert_task(eng, "хочет в кино", None, shared=True)
    assert await shared_items_summary("u1", engine=eng) == "хочет в кино"


@pytest.mark.asyncio
async def test_shared_task_today():
    eng = _engine()
    # fixed "now" (like test_booking_busy.py's T0) — real datetime.now() near
    # MSK midnight would flake "сегодня" into "завтра".
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)  # 12:00 MSK
    _insert_task(eng, "забрать посылку", now + timedelta(hours=2), shared=True)
    assert await shared_items_summary("u1", engine=eng, now=now) == "забрать посылку (сегодня)"


@pytest.mark.asyncio
async def test_shared_task_on_weekend():
    eng = _engine()
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)  # Tuesday 12:00 MSK
    saturday = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)  # Saturday 12:00 MSK
    _insert_task(eng, "хочет в кино", saturday, shared=True)
    assert await shared_items_summary("u1", engine=eng, now=now) == "хочет в кино (на выходных)"


@pytest.mark.asyncio
async def test_done_shared_task_excluded():
    eng = _engine()
    _insert_task(eng, "готово давно", datetime.now(UTC), shared=True, status="Done")
    assert await shared_items_summary("u1", engine=eng) == ""


@pytest.mark.asyncio
async def test_other_users_shared_tasks_excluded():
    eng = _engine()
    _insert_task(eng, "чужое", None, shared=True, user_id="u2")
    assert await shared_items_summary("u1", engine=eng) == ""


@pytest.mark.asyncio
async def test_multiple_shared_tasks_joined():
    eng = _engine()
    _insert_task(eng, "хочет в кино", None, shared=True)
    _insert_task(eng, "забрать посылку", None, shared=True)
    result = await shared_items_summary("u1", engine=eng)
    assert "хочет в кино" in result and "забрать посылку" in result
    assert "; " in result


@pytest.mark.asyncio
async def test_no_user_id_returns_empty():
    eng = _engine()
    _insert_task(eng, "хочет в кино", None, shared=True)
    assert await shared_items_summary("", engine=eng) == ""


# ── #242-b: "и подключи колонку к заре" — nexus_lists.shared ──────────────

@pytest.mark.asyncio
async def test_shared_list_item_without_date_has_no_hint():
    eng = _engine()
    _insert_list_item(eng, "заказать торт", None, shared=True)
    assert await shared_items_summary("u1", engine=eng) == "заказать торт"


@pytest.mark.asyncio
async def test_shared_list_item_with_expires_at_hint():
    eng = _engine()
    now = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)  # Tuesday
    _insert_list_item(eng, "забрать посылку", date(2026, 9, 15), shared=True)
    assert await shared_items_summary("u1", engine=eng, now=now) == "забрать посылку (сегодня)"


@pytest.mark.asyncio
async def test_done_shared_list_item_excluded():
    eng = _engine()
    _insert_list_item(eng, "готово", None, shared=True, status="done")
    assert await shared_items_summary("u1", engine=eng) == ""


@pytest.mark.asyncio
async def test_other_users_shared_list_items_excluded():
    eng = _engine()
    _insert_list_item(eng, "чужое", None, shared=True, user_id="u2")
    assert await shared_items_summary("u1", engine=eng) == ""


@pytest.mark.asyncio
async def test_private_list_item_excluded():
    eng = _engine()
    _insert_list_item(eng, "приватный пункт", None, shared=False)
    assert await shared_items_summary("u1", engine=eng) == ""


@pytest.mark.asyncio
async def test_tasks_and_list_items_combined():
    eng = _engine()
    _insert_task(eng, "хочет в кино", None, shared=True)
    _insert_list_item(eng, "заказать торт", None, shared=True)
    result = await shared_items_summary("u1", engine=eng)
    assert "хочет в кино" in result and "заказать торт" in result
