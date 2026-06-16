"""tests/test_pg_works_repo.py — unit tests for new PgWorksRepo methods.

Uses an in-memory SQLite DB (mirrors the schema via works_tables.py).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from arcana.repos.works_repo import Work
from arcana.repos.works_tables import metadata, work_priority, work_status, works


# ── Shared in-memory engine ──────────────────────────────────────────────────

@pytest.fixture
def engine():
    # StaticPool: all connections share one underlying DBAPI connection so
    # the in-memory DB persists across multiple engine.connect() calls.
    # Raw SQL: `works.client_id` FK points to `clients` in a different
    # MetaData object, so metadata.create_all() would fail on FK resolution.
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE clients (id INTEGER PRIMARY KEY)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE work_priority "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, "
            "emoji TEXT, label TEXT NOT NULL, sort INTEGER DEFAULT 0)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE work_status "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, "
            "emoji TEXT, label TEXT NOT NULL, sort INTEGER DEFAULT 0)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE works "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
            "deadline TIMESTAMP, reminder TIMESTAMP, category TEXT, "
            "priority_id INTEGER, status_id INTEGER, client_id INTEGER, "
            "user_notion_id TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    with eng.begin() as conn:
        conn.execute(work_priority.insert().values(
            [{"code": "urgent", "emoji": "🔴", "label": "Срочно", "sort": 0},
             {"code": "important", "emoji": "🟡", "label": "Важно", "sort": 1},
             {"code": "later", "emoji": "🟢", "label": "Можно потом", "sort": 2}]
        ))
        conn.execute(work_status.insert().values(
            [{"code": "open", "emoji": "⬜", "label": "В работе", "sort": 0},
             {"code": "done", "emoji": "✅", "label": "Готово", "sort": 1},
             {"code": "archived", "emoji": "🗄️", "label": "Архив", "sort": 2}]
        ))
    return eng


def _insert_work(engine, title="Тест", user="u1", status_code="open",
                 deadline=None, reminder=None, client_id=None):
    with engine.begin() as conn:
        status_row = conn.execute(
            sa.select(work_status.c.id).where(work_status.c.code == status_code)
        ).fetchone()
        prio_row = conn.execute(
            sa.select(work_priority.c.id).where(work_priority.c.code == "later")
        ).fetchone()
        row = conn.execute(
            works.insert().values(
                title=title, status_id=status_row[0],
                priority_id=prio_row[0], user_notion_id=user,
                deadline=deadline, reminder=reminder, client_id=client_id,
            ).returning(works.c.id)
        ).fetchone()
    return str(row[0])


# ── find_by_id ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_by_id_returns_work(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    wid = _insert_work(engine, title="Найди меня")
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        result = await repo.find_by_id(wid)
    assert result is not None
    assert result.title == "Найди меня"
    assert result.status == "open"
    assert isinstance(result, Work)


@pytest.mark.asyncio
async def test_find_by_id_returns_none_for_missing(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        result = await repo.find_by_id("99999")
    assert result is None


@pytest.mark.asyncio
async def test_find_by_id_invalid_id_returns_none(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        result = await repo.find_by_id("not-an-int")
    assert result is None


# ── list_all ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_all_returns_all_statuses(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    _insert_work(engine, title="Открытая", user="u1", status_code="open")
    _insert_work(engine, title="Готовая", user="u1", status_code="done")
    _insert_work(engine, title="Архив", user="u1", status_code="archived")
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        result = await repo.list_all("u1")
    assert len(result) == 3
    statuses = {w.status for w in result}
    assert statuses == {"open", "done", "archived"}


@pytest.mark.asyncio
async def test_list_all_filters_by_user(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    _insert_work(engine, title="Моя", user="u1")
    _insert_work(engine, title="Чужая", user="u2")
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        result = await repo.list_all("u1")
    assert len(result) == 1
    assert result[0].title == "Моя"


@pytest.mark.asyncio
async def test_list_all_empty_user_returns_all(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    _insert_work(engine, title="А", user="u1")
    _insert_work(engine, title="Б", user="u2")
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        result = await repo.list_all("")
    assert len(result) == 2


# ── set_status ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_status_done(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    wid = _insert_work(engine, title="Сделать", status_code="open")
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        ok = await repo.set_status(wid, "done")
        w = await repo.find_by_id(wid)
    assert ok is True
    assert w is not None
    assert w.status == "done"


@pytest.mark.asyncio
async def test_set_status_archived(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    wid = _insert_work(engine, title="Отменить", status_code="open")
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        ok = await repo.set_status(wid, "archived")
        w = await repo.find_by_id(wid)
    assert ok is True
    assert w.status == "archived"


@pytest.mark.asyncio
async def test_set_status_returns_false_for_missing(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        ok = await repo.set_status("99999", "done")
    assert ok is False


# ── set_deadline ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_deadline_updates_field(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    wid = _insert_work(engine, title="Дедлайн")
    new_date = date(2026, 12, 31)
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        ok = await repo.set_deadline(wid, new_date)
        w = await repo.find_by_id(wid)
    assert ok is True
    assert w is not None
    assert w.deadline_dt is not None
    assert w.deadline_dt.year == 2026
    assert w.deadline_dt.month == 12
    assert w.deadline_dt.day == 31


@pytest.mark.asyncio
async def test_set_deadline_returns_false_for_missing(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        ok = await repo.set_deadline("99999", date(2026, 1, 1))
    assert ok is False


# ── row_to_work extended fields ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_by_id_populates_deadline_iso(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    dl = datetime(2026, 8, 15, 9, 30, tzinfo=timezone.utc)
    wid = _insert_work(engine, title="Deadline test", deadline=dl)
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        w = await repo.find_by_id(wid)
    assert w is not None
    assert w.deadline_iso != ""
    assert "2026" in w.deadline_iso
    assert w.deadline_dt is not None
