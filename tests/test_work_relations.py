"""tests/test_work_relations.py — авто-привязка Работа↔Расклад/Ритуал на PG (#151).

- PgWorksRepo.find_active_for_client: открытая Работа клиента нужной категории
  (точный match category, status≠done/archived, client filter) — не цепляет чужую.
- work_relation: find → set_event_work_id (session/ritual dispatch) → close (set_status done).
- fail-closed по юзеру.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import BigInteger, Column, Table, create_engine
from sqlalchemy.pool import StaticPool

from arcana.repos.rituals_tables import metadata
from arcana.repos.works_tables import works, work_status, work_priority
from arcana.repos.works_repo import Work
import arcana.repos.pg_works_repo as pgw

_NOW = datetime(2026, 6, 1, 12, 0)


# ── find_active_for_client (реальный SQL на in-memory sqlite) ────────────────

@pytest.fixture
def works_engine(monkeypatch):
    clients_stub = Table("clients", metadata, Column("id", BigInteger, primary_key=True))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    try:
        metadata.create_all(engine, tables=[work_status, work_priority, clients_stub, works])
        with engine.begin() as c:
            for sid, code in [(1, "open"), (2, "done"), (3, "archived")]:
                c.execute(work_status.insert().values(id=sid, code=code, label=code))
            c.execute(work_priority.insert().values(id=1, code="later", label="Можно потом"))
            base = dict(priority_id=1, created_at=_NOW, updated_at=_NOW)
            # open расклад клиента 5 — ЦЕЛЬ
            c.execute(works.insert().values(id=1, title="расклад Оле", category="🃏 Расклад",
                      client_id=5, status_id=1, **base))
            # open ритуал клиента 5
            c.execute(works.insert().values(id=2, title="ритуал Оле", category="✨ Ритуал",
                      client_id=5, status_id=1, **base))
            # done расклад клиента 5 — НЕ цель
            c.execute(works.insert().values(id=3, title="старый расклад", category="🃏 Расклад",
                      client_id=5, status_id=2, **base))
            # archived расклад клиента 5 — НЕ цель
            c.execute(works.insert().values(id=5, title="архив", category="🃏 Расклад",
                      client_id=5, status_id=3, **base))
            # open расклад ЧУЖОГО клиента 9
            c.execute(works.insert().values(id=4, title="чужой", category="🃏 Расклад",
                      client_id=9, status_id=1, **base))
        monkeypatch.setattr(pgw, "get_engine", lambda: engine)
        yield engine
    finally:
        metadata.remove(clients_stub)


@pytest.mark.asyncio
async def test_find_active_matches_open_same_category_client(works_engine):
    repo = pgw.PgWorksRepo()
    w = await repo.find_active_for_client("5", "🃏 Расклад", "")
    assert w is not None and w.id == "1"          # open расклад клиента 5
    w2 = await repo.find_active_for_client("5", "✨ Ритуал", "")
    assert w2 is not None and w2.id == "2"


@pytest.mark.asyncio
async def test_find_active_excludes_done_archived_and_other_client(works_engine):
    repo = pgw.PgWorksRepo()
    # done(3)/archived(5) не возвращаются; чужой клиент 9 не цепляется к 5
    w = await repo.find_active_for_client("5", "🃏 Расклад", "")
    assert w.id not in ("3", "5", "4")
    # категория не совпала → не закрываем чужую → None
    assert await repo.find_active_for_client("5", "🍕 Прочее", "") is None
    # чужой клиент 9 — своя работа
    other = await repo.find_active_for_client("9", "🃏 Расклад", "")
    assert other is not None and other.id == "4"


# ── work_relation orchestration (mocked repos) ───────────────────────────────

@pytest.mark.asyncio
async def test_work_relation_dispatch_and_close():
    from core import work_relation as wr
    from arcana.repos.pg_works_repo import PgWorksRepo
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    from arcana.repos.pg_rituals_repo import PgRitualsRepo

    work = Work(id="9", title="W", priority="Можно потом",
                deadline_str="", category_str="", has_client=True)

    with patch.object(PgWorksRepo, "find_active_for_client", AsyncMock(return_value=work)) as m_find, \
         patch.object(PgSessionsRepo, "set_work_id", AsyncMock(return_value=True)) as m_s, \
         patch.object(PgRitualsRepo, "set_work_id", AsyncMock(return_value=True)) as m_r, \
         patch.object(PgWorksRepo, "set_status", AsyncMock(return_value=True)) as m_close:
        wid = await wr.find_active_work_for_client("5", "🃏 Расклад", "u-1")
        assert wid == "9"
        m_find.assert_awaited_once_with("5", "🃏 Расклад", "u-1")

        assert await wr.set_event_work_id("session", "s1", "9") is True
        m_s.assert_awaited_once_with("s1", "9")

        assert await wr.set_event_work_id("ritual", "r1", "9") is True
        m_r.assert_awaited_once_with("r1", "9")

        assert await wr.close_work_as_done("9") is True
        m_close.assert_awaited_once_with("9", "done")


@pytest.mark.asyncio
async def test_find_active_work_fail_closed_no_user():
    from core import work_relation as wr
    from arcana.repos.pg_works_repo import PgWorksRepo
    with patch.object(PgWorksRepo, "find_active_for_client", AsyncMock()) as m_find:
        assert await wr.find_active_work_for_client("5", "🃏 Расклад", "") is None
        assert await wr.find_active_work_for_client("", "🃏 Расклад", "u") is None
    m_find.assert_not_called()
