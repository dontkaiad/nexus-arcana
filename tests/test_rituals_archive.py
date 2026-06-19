"""tests/test_rituals_archive.py — soft-archive ритуалов (PgRitualsRepo.archive).

Поведение:
- archive(id) ставит archived=True (запись остаётся в БД);
- заархивированный ритуал пропадает из list_all (и list_by_client);
- find_by_id всё ещё находит его.

Изолированная in-memory SQLite (StaticPool — общий коннекшен для to_thread),
схема из rituals_tables.metadata, get_engine монкейпатчится на тестовый движок.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from arcana.repos.rituals_tables import (
    metadata, rituals,
    outcome_status, magical_purpose, ritual_place,
    engagement_type, payment_source,
)
import arcana.repos.pg_rituals_repo as pgr

# rituals_tables.metadata is SHARED with works/grimoire (which FK to `clients`,
# a table in another metadata) — so create only the rituals-slice tables.
_RITUAL_SLICE = [
    outcome_status, magical_purpose, ritual_place,
    engagement_type, payment_source, rituals,
]

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo(monkeypatch):
    """In-memory движок + один посеянный ритуал (id=1, client_id=7)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine, tables=_RITUAL_SLICE)
    with engine.begin() as conn:
        conn.execute(rituals.insert().values(
            id=1, title="Очищение", client_id=7,
            occurred_at=_NOW, created_at=_NOW, updated_at=_NOW,
        ))
    monkeypatch.setattr(pgr, "get_engine", lambda: engine)
    r = pgr.PgRitualsRepo()
    r._engine = engine  # на случай прямых проверок
    return r


@pytest.mark.asyncio
async def test_archive_sets_flag(repo):
    ok = await repo.archive("1")
    assert ok is True
    with repo._engine.connect() as conn:
        flag = conn.execute(
            select(rituals.c.archived).where(rituals.c.id == 1)
        ).fetchone()[0]
    assert bool(flag) is True


@pytest.mark.asyncio
async def test_archived_excluded_from_list_all(repo):
    before = await repo.list_all()
    assert len(before) == 1
    await repo.archive("1")
    after = await repo.list_all()
    assert after == [] or all(r.id != "1" for r in after)


@pytest.mark.asyncio
async def test_archived_excluded_from_list_by_client(repo):
    before = await repo.list_by_client("7")
    assert len(before) == 1
    await repo.archive("1")
    after = await repo.list_by_client("7")
    assert all(r.id != "1" for r in after)


@pytest.mark.asyncio
async def test_archived_still_found_by_id(repo):
    await repo.archive("1")
    found = await repo.find_by_id("1")
    assert found is not None
    assert found.id == "1"


@pytest.mark.asyncio
async def test_archive_bad_id_returns_false(repo):
    assert await repo.archive("not-an-int") is False
