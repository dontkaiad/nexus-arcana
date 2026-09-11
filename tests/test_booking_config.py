"""tests/test_booking_config.py — availability / meeting-type / block CRUD
(core/booking/repo.py, #23 / ADR-0026). In-memory SQLite, engine injected."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.booking import repo

UTC = timezone.utc


def _make_engine():
    eng = sa.create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE booking_availability (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "context TEXT NOT NULL, weekday INTEGER, specific_date TEXT, start_time TEXT NOT NULL, end_time TEXT NOT NULL, "
            "tz TEXT NOT NULL DEFAULT 'Europe/Moscow', slot_minutes INTEGER NOT NULL DEFAULT 60, "
            "min_notice_hours INTEGER NOT NULL DEFAULT 12, max_advance_days INTEGER NOT NULL DEFAULT 60, "
            "buffer_before_min INTEGER NOT NULL DEFAULT 0, buffer_after_min INTEGER NOT NULL DEFAULT 0, "
            "active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT)"))
        c.execute(sa.text(
            "CREATE TABLE booking_meeting_type (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "context TEXT NOT NULL, slug TEXT NOT NULL, title TEXT DEFAULT '', duration_min INTEGER DEFAULT 60, "
            "location_kind TEXT DEFAULT 'call', location_value TEXT DEFAULT '', requires_approval INTEGER DEFAULT 0, "
            "description TEXT DEFAULT '', color TEXT DEFAULT '', active INTEGER DEFAULT 1, "
            "created_at TEXT, updated_at TEXT)"))
        c.execute(sa.text(
            "CREATE TABLE booking_block (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT DEFAULT '', "
            "start_at TEXT NOT NULL, end_at TEXT NOT NULL, reason TEXT DEFAULT '', created_at TEXT)"))
    return eng


@pytest.mark.asyncio
async def test_availability_crud():
    eng = _make_engine()
    assert await repo.list_availability("u1", engine=eng) == []
    row = await repo.add_availability("u1", {
        "context": "friends", "weekday": 5, "start_time": "12:00", "end_time": "18:00",
        "slot_minutes": 60, "junk": "ignored",
    }, engine=eng)
    assert row["id"] and row["weekday"] == 5 and "junk" not in row
    rows = await repo.list_availability("u1", engine=eng)
    assert len(rows) == 1
    upd = await repo.edit_availability(row["id"], "u1", {"active": False, "slot_minutes": 30}, engine=eng)
    assert upd["active"] in (0, False) and upd["slot_minutes"] == 30
    assert await repo.del_availability(row["id"], "u1", engine=eng) is True
    assert await repo.list_availability("u1", engine=eng) == []
    # delete of a non-owner row → False
    assert await repo.del_availability(999, "u1", engine=eng) is False


@pytest.mark.asyncio
async def test_availability_specific_date_coerced_from_iso_string():
    """#232: разовое окно на дату — specific_date идёт строкой "YYYY-MM-DD"
    с фронта, repo коэрсит в date() как и start_time/end_time."""
    eng = _make_engine()
    row = await repo.add_availability("u1", {
        "context": "arcana", "specific_date": "2026-09-16",
        "start_time": "12:00", "end_time": "18:00",
    }, engine=eng)
    assert row["id"] and str(row["specific_date"]) == "2026-09-16"
    assert row["weekday"] is None


@pytest.mark.asyncio
async def test_meeting_type_crud_and_owner_scope():
    eng = _make_engine()
    a = await repo.add_meeting_type("u1", {"context": "friends", "slug": "coffee", "title": "Кофе"}, engine=eng)
    await repo.add_meeting_type("u2", {"context": "friends", "slug": "other", "title": "X"}, engine=eng)
    mine = await repo.list_meeting_types("u1", engine=eng)
    assert [m["slug"] for m in mine] == ["coffee"]
    assert await repo.edit_meeting_type(a["id"], "u1", {"duration_min": 90}, engine=eng)
    assert (await repo.list_meeting_types("u1", engine=eng))[0]["duration_min"] == 90


@pytest.mark.asyncio
async def test_block_crud():
    eng = _make_engine()
    s = datetime(2026, 10, 1, tzinfo=UTC)
    b = await repo.add_block("u1", s, s + timedelta(days=3), "отпуск", engine=eng)
    assert b["reason"] == "отпуск"
    assert len(await repo.list_blocks("u1", engine=eng)) == 1
    assert await repo.del_block(b["id"], "u1", engine=eng) is True
