"""tests/test_budget_memory_pg.py — бюджет-память пишется/читается из PG (#145).

_save_memory_entry → _mem_repo.upsert с единой категорией «💰 Лимит» (как
натуральный путь core.memory), БЕЗ Notion db_query/page_create/update_page.
_deactivate_goal → find_by_key_prefixes + set_active(False).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.repos.pg_memory_repo import Memory


@pytest.mark.asyncio
async def test_save_memory_entry_writes_pg_unified_category():
    from nexus.handlers.finance import _save_memory_entry

    with patch("core.repos.memory_repo._repo.upsert",
               AsyncMock(return_value=("m-1", False))) as m_upsert:
        await _save_memory_entry("постоянно_жильё_квартира",
                                 "постоянно: квартира — 25000₽/мес", "u-1")

    m_upsert.assert_awaited_once()
    args = m_upsert.call_args.args
    # (fact, key, category, scope, related_to, source, user_id)
    assert args[1] == "постоянно_жильё_квартира"
    assert args[2] == "💰 Лимит"          # ЕДИНАЯ категория, НЕ «🔒 Постоянные»
    assert args[3] == "nexus"
    assert args[5] == "manual"
    assert args[6] == "u-1"


@pytest.mark.asyncio
async def test_save_memory_entry_fail_closed_no_user():
    from nexus.handlers.finance import _save_memory_entry

    with patch("core.repos.memory_repo._repo.upsert", AsyncMock()) as m_upsert:
        await _save_memory_entry("лимит_кафе", "лимит: Кафе — 5000₽/мес", "")

    m_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_deactivate_goal_via_goals_repo():
    """#205: _deactivate_goal → pg_goals_repo.set_status (не Память)."""
    from nexus.handlers.finance import _deactivate_goal

    with patch("core.repos.pg_goals_repo._repo.set_status",
               AsyncMock(return_value=True)) as m_ss:
        ok = await _deactivate_goal("телефон", "u-1", achieved=True)
    assert ok is True
    m_ss.assert_awaited_once_with("u-1", "телефон", "achieved")

    with patch("core.repos.pg_goals_repo._repo.set_status",
               AsyncMock(return_value=True)) as m_ss:
        await _deactivate_goal("ноут", "u-1")
    m_ss.assert_awaited_once_with("u-1", "ноут", "dropped")


@pytest.mark.asyncio
async def test_deactivate_goal_no_user():
    from nexus.handlers.finance import _deactivate_goal
    with patch("core.repos.pg_goals_repo._repo.set_status", AsyncMock()) as m_ss:
        ok = await _deactivate_goal("телефон", "")
    assert ok is False
    m_ss.assert_not_called()


# ── БАГ 1: долг из обычного save_memory пишется в debts table, не в Память ────

@pytest.mark.asyncio
async def test_save_memory_debt_writes_debts_table_not_memory():
    """save_memory() с ключом 'долг_...' → pg_debts_repo.upsert(kind='i_owe'),
    БЕЗ записи в Память (иначе load_budget_data его не видит)."""
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()

    fact = "долг: 👩 Подружка — 50000₽ · дедлайн: апрель 2026"
    with patch("core.memory._parse_fact",
               AsyncMock(return_value=(fact, "💰 Лимит", "подружка", "долг_подружка"))), \
         patch("core.repos.pg_debts_repo._repo.upsert", AsyncMock()) as m_debt, \
         patch.object(cmem._mem_repo, "add", AsyncMock()) as m_add, \
         patch.object(cmem._mem_repo, "upsert", AsyncMock()) as m_up:
        await save_memory(msg, "долг подружке 50000 до апреля", "u-1", "☀️ Nexus")

    m_add.assert_not_awaited()
    m_up.assert_not_awaited()
    m_debt.assert_awaited_once()
    args, kwargs = m_debt.call_args
    assert args[0] == "u-1"
    assert args[1] == "подружка"
    assert args[2] == "i_owe"
    assert kwargs["amount"] == 50000.0
    assert kwargs["deadline"] == "апрель 2026"
    msg.answer.assert_awaited_once_with(f"📋 Добавил долг: {fact}")


@pytest.mark.asyncio
async def test_save_memory_goal_writes_goals_table_not_memory():
    """#205: save_memory() с ключом 'цель_...' → pg_goals_repo.upsert, БЕЗ Памяти."""
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()

    fact = "цель: 💻 ПК — 200000₽ · откладываю 15000₽/мес"
    with patch("core.memory._parse_fact",
               AsyncMock(return_value=(fact, "💰 Лимит", "пк", "цель_пк"))), \
         patch("core.repos.pg_goals_repo._repo.upsert", AsyncMock()) as m_goal, \
         patch.object(cmem._mem_repo, "add", AsyncMock()) as m_add, \
         patch.object(cmem._mem_repo, "upsert", AsyncMock()) as m_up:
        await save_memory(msg, "цель ПК 200000", "u-1", "☀️ Nexus")

    m_add.assert_not_awaited()
    m_up.assert_not_awaited()
    m_goal.assert_awaited_once()
    args, kwargs = m_goal.call_args
    assert args[0] == "u-1" and args[1] == "💻 ПК"
    assert kwargs["target"] == 200000.0 and kwargs["monthly"] == 15000.0
    msg.answer.assert_awaited_once_with(f"🎯 Добавил цель: {fact}")


@pytest.mark.asyncio
async def test_save_memory_goal_podushka_stays_memory():
    """цель_подушка — НЕ цель (отдельный трекер), в goals не уходит."""
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()
    with patch("core.memory._parse_fact",
               AsyncMock(return_value=("цель: подушка — 300000₽", "💰 Лимит", "подушка", "цель_подушка"))), \
         patch("core.repos.pg_goals_repo._repo.upsert", AsyncMock()) as m_goal, \
         patch.object(cmem._mem_repo, "upsert", AsyncMock(return_value=("m1", False))):
        await save_memory(msg, "цель подушка 300000", "u-1", "☀️ Nexus")
    m_goal.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_memory_debt_without_deadline():
    import core.memory as cmem
    from core.memory import save_memory

    msg = AsyncMock()
    msg.answer = AsyncMock()

    fact = "долг: 👤 Друг — 40000₽"
    with patch("core.memory._parse_fact",
               AsyncMock(return_value=(fact, "💰 Лимит", "друг", "долг_друг"))), \
         patch("core.repos.pg_debts_repo._repo.upsert", AsyncMock()) as m_debt, \
         patch.object(cmem._mem_repo, "add", AsyncMock()) as m_add, \
         patch.object(cmem._mem_repo, "upsert", AsyncMock()) as m_up:
        await save_memory(msg, "долг другу 40000", "u-1", "☀️ Nexus")

    m_add.assert_not_awaited()
    m_up.assert_not_awaited()
    m_debt.assert_awaited_once()
    args, kwargs = m_debt.call_args
    assert args[1] == "друг"
    assert kwargs["amount"] == 40000.0
    assert kwargs["deadline"] is None
