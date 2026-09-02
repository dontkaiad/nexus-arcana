"""tests/test_pg_cushion_repo.py — PgCushionRepo unit tests (#подушка).

Подушка — отдельная сущность. balance инкрементится (не перезаписывается),
target/monthly_contribution правятся. Один ряд на пользователя.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.repos.pg_cushion_repo import PgCushionRepo


def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE cushion ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_notion_id TEXT NOT NULL DEFAULT '', "
            "balance REAL NOT NULL DEFAULT 0, "
            "target REAL, "
            "monthly_contribution REAL NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE cushion_transactions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_notion_id TEXT NOT NULL DEFAULT '', "
            "amount REAL NOT NULL, "
            "source TEXT NOT NULL DEFAULT 'manual', "
            "note TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    return eng


@pytest.mark.asyncio
async def test_get_none_when_no_row():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        assert await PgCushionRepo().get("u1") is None


@pytest.mark.asyncio
async def test_add_to_balance_increments_not_overwrites():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        repo = PgCushionRepo()
        assert await repo.add_to_balance("u1", 5000) == 5000
        assert await repo.add_to_balance("u1", 3000) == 8000
        assert await repo.add_to_balance("u1", 2500) == 10500
        c = await repo.get("u1")
        assert c.balance == 10500
        # ровно один ряд на пользователя
        with eng.connect() as conn:
            n = conn.execute(sa.text("SELECT COUNT(*) FROM cushion WHERE user_notion_id='u1'")).scalar()
        assert n == 1


@pytest.mark.asyncio
async def test_set_target_and_monthly_do_not_touch_balance():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        repo = PgCushionRepo()
        await repo.add_to_balance("u1", 7000)
        await repo.set_target("u1", 300000)
        await repo.set_monthly_contribution("u1", 8000)
        c = await repo.get("u1")
        assert c.balance == 7000
        assert c.target == 300000
        assert c.monthly_contribution == 8000
        # повторное принятие плана только меняет взнос, баланс не трогает
        await repo.set_monthly_contribution("u1", 5000)
        c = await repo.get("u1")
        assert c.balance == 7000 and c.monthly_contribution == 5000


@pytest.mark.asyncio
async def test_add_to_balance_logs_transaction():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        repo = PgCushionRepo()
        await repo.add_to_balance("u1", 5000, source="manual", note="")
        await repo.add_to_balance("u1", 8000, source="payday_auto", note="взнос за период 2026-08")
        txs, has_more = await repo.list_transactions("u1", limit=10, offset=0)
        assert has_more is False
        assert [(t.amount, t.source) for t in txs] == [
            (8000.0, "payday_auto"), (5000.0, "manual"),  # новые первыми
        ]
        assert txs[0].note == "взнос за период 2026-08"


@pytest.mark.asyncio
async def test_list_transactions_pagination():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        repo = PgCushionRepo()
        for i in range(5):
            await repo.add_to_balance("u1", 100 * (i + 1))
        page0, more0 = await repo.list_transactions("u1", limit=2, offset=0)
        page1, more1 = await repo.list_transactions("u1", limit=2, offset=2)
        assert len(page0) == 2 and more0 is True
        assert len(page1) == 2 and more1 is True
        page2, more2 = await repo.list_transactions("u1", limit=2, offset=4)
        assert len(page2) == 1 and more2 is False


@pytest.mark.asyncio
async def test_set_target_does_not_log_transaction():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        repo = PgCushionRepo()
        await repo.set_target("u1", 300000)
        txs, _ = await repo.list_transactions("u1", limit=10, offset=0)
        assert txs == []


@pytest.mark.asyncio
async def test_users_isolated():
    eng = _make_engine()
    with patch("core.repos.pg_cushion_repo._get_engine", return_value=eng):
        repo = PgCushionRepo()
        await repo.add_to_balance("u1", 5000)
        await repo.add_to_balance("u2", 100)
        assert (await repo.get("u1")).balance == 5000
        assert (await repo.get("u2")).balance == 100
