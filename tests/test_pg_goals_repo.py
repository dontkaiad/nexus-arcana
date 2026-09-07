"""tests/test_pg_goals_repo.py — PgGoalsRepo unit tests (#205).

In-memory SQLite + StaticPool (как test_pg_debts_repo.py).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.repos.pg_goals_repo import PgGoalsRepo


def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE goals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id TEXT NOT NULL DEFAULT '', "
            "name TEXT NOT NULL, "
            "target REAL NOT NULL, "
            "monthly REAL NOT NULL DEFAULT 0, "
            "saved REAL NOT NULL DEFAULT 0, "
            "status TEXT NOT NULL DEFAULT 'active', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "closed_at TIMESTAMP)"
        ))
    return eng


@pytest.fixture
def repo():
    eng = _make_engine()
    with patch("core.repos.pg_goals_repo._get_engine", return_value=eng):
        yield PgGoalsRepo()


@pytest.mark.asyncio
async def test_upsert_create_then_update_case_insensitive(repo):
    await repo.upsert("u1", "Ноутбук", target=200000, monthly=15000)
    await repo.upsert("u1", "ноутбук", target=180000, monthly=10000)  # тот же (lower)
    active = await repo.list_active("u1")
    assert len(active) == 1
    assert active[0].target == 180000 and active[0].monthly == 10000


@pytest.mark.asyncio
async def test_set_status_closes_and_drops_from_active(repo):
    await repo.upsert("u1", "Телефон", target=100000)
    assert await repo.set_status("u1", "телефон", "dropped") is True
    assert await repo.list_active("u1") == []
    closed = await repo.list_closed("u1")
    assert len(closed) == 1 and closed[0].status == "dropped" and closed[0].closed_at
    # повторно — уже не active → False
    assert await repo.set_status("u1", "телефон", "achieved") is False


@pytest.mark.asyncio
async def test_add_saved_auto_achieves_on_target(repo):
    await repo.upsert("u1", "ПК", target=100000)
    assert await repo.add_saved("u1", "ПК", 40000) == 40000
    assert (await repo.list_active("u1"))[0].saved == 40000
    # добиваем до цели → achieved
    assert await repo.add_saved("u1", "ПК", 70000) == 110000
    assert await repo.list_active("u1") == []
    assert (await repo.list_closed("u1"))[0].status == "achieved"


@pytest.mark.asyncio
async def test_add_saved_missing_goal_returns_none(repo):
    assert await repo.add_saved("u1", "нет", 100) is None


@pytest.mark.asyncio
async def test_scoped_by_user(repo):
    await repo.upsert("u1", "A", target=1000)
    await repo.upsert("u2", "B", target=2000)
    assert [g.name for g in await repo.list_active("u1")] == ["A"]
