"""tests/test_debt_cutover.py — i_owe cutover regression (#8 шаг 3).

4 mock tests (unit):
  1. _save_debt → upsert(kind="i_owe", amount, deadline)
  2. _partial_debt_payment → reduce_amount; returns int(new_amount); (0,True) → 0
  3. _deactivate_debt → deactivate called; bool propagated
  4. load_budget_data → list_active(kind="i_owe"); strategy + monthly_payment in dict

2 SQLite integration tests (end-to-end via same SQLite engine):
  5. CONSISTENCY: _save_debt → load_budget_data sees debt
  6. CLOSED: create → deactivate → _load_closed_budget sees closed debt
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool


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


# ── Test 1: _save_debt calls upsert with kind="i_owe" ────────────────────────

@pytest.mark.asyncio
async def test_save_debt_calls_upsert_i_owe():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import _save_debt

    with patch.object(drmod._repo, "upsert", new_callable=AsyncMock) as mock_upsert:
        await _save_debt("Аня", 5000, "июнь 2026", user_notion_id="uid1")

    mock_upsert.assert_called_once()
    args, kwargs = mock_upsert.call_args
    assert args[0] == "uid1"
    assert args[1] == "Аня"
    assert args[2] == "i_owe"
    assert kwargs["amount"] == 5000.0
    assert kwargs["deadline"] == "июнь 2026"


# ── Test 2: _partial_debt_payment → reduce_amount; (0, True) → 0 ─────────────

@pytest.mark.asyncio
async def test_partial_debt_payment_returns_int_new_amount():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import _partial_debt_payment

    with patch.object(drmod._repo, "reduce_amount",
                      new_callable=AsyncMock, return_value=(7000.0, False)):
        result = await _partial_debt_payment("Аня", 3000, user_notion_id="uid1")

    assert result == 7000


@pytest.mark.asyncio
async def test_partial_debt_payment_zero_when_closed():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import _partial_debt_payment

    with patch.object(drmod._repo, "reduce_amount",
                      new_callable=AsyncMock, return_value=(0.0, True)):
        result = await _partial_debt_payment("Аня", 99999, user_notion_id="uid1")

    assert result == 0


@pytest.mark.asyncio
async def test_partial_debt_payment_none_if_not_found():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import _partial_debt_payment

    with patch.object(drmod._repo, "reduce_amount",
                      new_callable=AsyncMock, return_value=None):
        result = await _partial_debt_payment("Несуществующий", 100, user_notion_id="uid1")

    assert result is None


# ── Test 3: _deactivate_debt → deactivate called; bool propagated ─────────────

@pytest.mark.asyncio
async def test_deactivate_debt_propagates_bool():
    import core.repos.pg_debts_repo as drmod
    from nexus.handlers.finance import _deactivate_debt

    with patch.object(drmod._repo, "deactivate",
                      new_callable=AsyncMock, return_value=True) as mock_deact:
        result = await _deactivate_debt("Аня", user_notion_id="uid1")

    assert result is True
    mock_deact.assert_called_once_with("uid1", "i_owe", "Аня")


# ── Test 4: load_budget_data reads debts from list_active ────────────────────

@pytest.mark.asyncio
async def test_load_budget_data_uses_list_active_for_debts():
    import core.repos.pg_debts_repo as drmod
    import core.repos.memory_repo as mrmod
    from core.repos.pg_debts_repo import Debt
    from core.budget import load_budget_data

    fake_debts = [
        Debt(id="1", user_notion_id="uid1", name="Банк", kind="i_owe",
             amount=120000.0, deadline="декабрь 2026",
             strategy="лавина", monthly_payment=15000.0,
             is_active=True, created_at="", updated_at=""),
    ]

    with patch.object(mrmod._repo, "find_by_key_prefixes", AsyncMock(return_value=[])):
        with patch.object(drmod._repo, "list_active",
                          new_callable=AsyncMock, return_value=fake_debts) as mock_la:
            data = await load_budget_data("uid1")

    mock_la.assert_called_once_with("uid1", kind="i_owe")
    assert len(data["долги"]) == 1
    d = data["долги"][0]
    assert d["name"] == "Банк"
    assert d["amount"] == 120000.0
    assert d["deadline"] == "декабрь 2026"
    assert d["strategy"] == "лавина"
    assert d["monthly_payment"] == 15000.0


# ── Test 5: CONSISTENCY via SQLite ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_debt_visible_in_load_budget_data():
    """End-to-end: _save_debt persists → load_budget_data retrieves via same SQLite engine."""
    import core.repos.pg_debts_repo as drmod
    import core.repos.memory_repo as mrmod
    from nexus.handlers.finance import _save_debt
    from core.budget import load_budget_data

    eng = _make_engine()

    with patch.object(drmod, "_get_engine", return_value=eng):
        with patch.object(mrmod._repo, "find_by_key_prefixes", AsyncMock(return_value=[])):
            await _save_debt("Аня", 5000, "июль 2026", user_notion_id="uid_cons")
            data = await load_budget_data("uid_cons")

    assert len(data["долги"]) == 1
    d = data["долги"][0]
    assert d["name"] == "Аня"
    assert d["amount"] == 5000.0
    assert d["deadline"] == "июль 2026"


# ── Test 6: CLOSED via SQLite ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivated_debt_visible_in_load_closed_budget():
    """End-to-end: create → deactivate → _load_closed_budget sees closed debt."""
    import core.repos.pg_debts_repo as drmod
    import miniapp.backend.routes.finance as fin_mod
    from nexus.handlers.finance import _save_debt, _deactivate_debt
    from miniapp.backend.routes.finance import _load_closed_budget

    eng = _make_engine()

    with patch.object(drmod, "_get_engine", return_value=eng):
        with patch.object(fin_mod._mem_repo, "find_by_category", AsyncMock(return_value=[])):
            await _save_debt("Петя", 10000, "октябрь 2026", user_notion_id="uid_cl")
            await _deactivate_debt("Петя", user_notion_id="uid_cl")
            result = await _load_closed_budget("uid_cl")

    assert len(result["долги"]) == 1
    d = result["долги"][0]
    assert d["name"] == "Петя"
    assert d["total"] == 10000
    assert d["left"] == 0
    assert result["цели"] == []
