"""tests/test_pg_debts_repo.py — PgDebtsRepo unit tests (#8).

Покрытие:
  1. upsert создаёт новую строку.
  2. upsert тем же именем в другом регистре → одна строка, amount обновился.
  3. i_owe и they_owe с одним именем → ДВЕ строки (kind разделяет).
  4. reduce_amount уменьшает; при amount <= 0 → is_active=False, closed=True.
  5. deactivate → is_active=False; повторный deactivate → False (не найден).
  6. list_active фильтрует по is_active.
  7. list_active фильтрует по kind.

Uses in-memory SQLite with StaticPool (same pattern as test_pg_lists_repo.py).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.repos.pg_debts_repo import Debt, PgDebtsRepo


# ── Shared engine ─────────────────────────────────────────────────────────────

def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE debts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_notion_id TEXT NOT NULL DEFAULT '', "
            "name TEXT NOT NULL, "
            "kind TEXT NOT NULL DEFAULT 'i_owe', "
            "amount REAL NOT NULL, "
            "deadline TEXT, "
            "strategy TEXT, "
            "monthly_payment REAL NOT NULL DEFAULT 0, "
            "is_active INTEGER NOT NULL DEFAULT 1, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    return eng


def _count(engine, user="u1") -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT COUNT(*) FROM debts WHERE user_notion_id = :u"),
            {"u": user},
        ).scalar()


def _get_row(engine, user="u1", name=None, kind="i_owe"):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT * FROM debts "
                "WHERE user_notion_id=:u AND lower(name)=lower(:n) AND kind=:k"
            ),
            {"u": user, "n": name, "k": kind},
        ).fetchone()


# ── Test 1: upsert создаёт строку ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_creates_row():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Маша", "i_owe", amount=50000, deadline="июнь 2026")

    assert _count(eng) == 1
    row = _get_row(eng, name="Маша")
    assert row is not None
    assert float(row.amount) == 50000.0
    assert row.deadline == "июнь 2026"
    assert bool(row.is_active)


# ── Test 2: upsert другой регистр → одна строка, поля обновились ─────────────

@pytest.mark.asyncio
async def test_upsert_case_insensitive_updates_not_duplicates():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Маша", "i_owe", amount=50000)
        await repo.upsert("u1", "маша", "i_owe", amount=30000, strategy="лавина", monthly_payment=10000)

    assert _count(eng) == 1, "«Маша» и «маша» должны быть одной строкой"
    row = _get_row(eng, name="Маша")
    assert float(row.amount) == 30000.0
    assert row.strategy == "лавина"
    assert float(row.monthly_payment) == 10000.0


# ── Test 3: i_owe и they_owe с одним именем → две строки ────────────────────

@pytest.mark.asyncio
async def test_upsert_different_kind_creates_two_rows():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Петя", "i_owe", amount=5000)
        await repo.upsert("u1", "Петя", "they_owe", amount=2000)

    assert _count(eng) == 2, "i_owe и they_owe с одним именем → две строки"
    row_owe = _get_row(eng, name="Петя", kind="i_owe")
    row_they = _get_row(eng, name="Петя", kind="they_owe")
    assert float(row_owe.amount) == 5000.0
    assert float(row_they.amount) == 2000.0


# ── Test 4: reduce_amount уменьшает; при <= 0 → closed ──────────────────────

@pytest.mark.asyncio
async def test_reduce_amount_partial():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Аня", "i_owe", amount=10000)
        result = await repo.reduce_amount("u1", "i_owe", "Аня", payment=3000)

    assert result is not None
    new_amount, closed = result
    assert new_amount == 7000.0
    assert closed is False
    row = _get_row(eng, name="Аня")
    assert bool(row.is_active) is True
    assert float(row.amount) == 7000.0


@pytest.mark.asyncio
async def test_reduce_amount_closes_when_zero():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Аня", "i_owe", amount=5000)
        result = await repo.reduce_amount("u1", "i_owe", "Аня", payment=6000)

    assert result is not None
    new_amount, closed = result
    assert new_amount == 0.0
    assert closed is True
    row = _get_row(eng, name="Аня")
    assert bool(row.is_active) is False


@pytest.mark.asyncio
async def test_reduce_amount_returns_none_if_not_found():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        result = await repo.reduce_amount("u1", "i_owe", "Несуществующий", payment=100)

    assert result is None


# ── Test 5: deactivate ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_sets_inactive():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Кредит", "i_owe", amount=100000)
        found = await repo.deactivate("u1", "i_owe", "Кредит")

    assert found is True
    row = _get_row(eng, name="Кредит")
    assert bool(row.is_active) is False


@pytest.mark.asyncio
async def test_deactivate_returns_false_if_not_found():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        found = await repo.deactivate("u1", "i_owe", "Призрак")

    assert found is False


# ── Test 6: list_active фильтрует по is_active ───────────────────────────────

@pytest.mark.asyncio
async def test_list_active_excludes_inactive():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Активный", "i_owe", amount=1000)
        await repo.upsert("u1", "Закрытый", "i_owe", amount=500)
        await repo.deactivate("u1", "i_owe", "Закрытый")
        result = await repo.list_active("u1")

    assert len(result) == 1
    assert result[0].name == "Активный"
    assert isinstance(result[0], Debt)


# ── Test 7: list_active фильтрует по kind ────────────────────────────────────

@pytest.mark.asyncio
async def test_list_active_filters_by_kind():
    eng = _make_engine()
    repo = PgDebtsRepo()
    with patch("core.repos.pg_debts_repo._get_engine", return_value=eng):
        await repo.upsert("u1", "Маша", "i_owe", amount=5000)
        await repo.upsert("u1", "Петя", "they_owe", amount=2000)
        await repo.upsert("u1", "Аня", "i_owe", amount=8000)

        i_owe_list = await repo.list_active("u1", kind="i_owe")
        they_owe_list = await repo.list_active("u1", kind="they_owe")
        all_list = await repo.list_active("u1")

    assert len(i_owe_list) == 2
    assert all(d.kind == "i_owe" for d in i_owe_list)
    assert len(they_owe_list) == 1
    assert they_owe_list[0].name == "Петя"
    assert len(all_list) == 3
