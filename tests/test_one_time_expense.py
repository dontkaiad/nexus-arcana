"""tests/test_one_time_expense.py — разовые расходы (новая категория).

Разовый расход — обязателен в этом месяце, но НЕ повторяется. Роутится в
finance-транзакцию (💸 Расход), НЕ в память → не пересчитывается в бюджете
следующего периода, но попадает в spending_by_category → already_spent.

Плюс регресс переименования обязательные→постоянные: classify() матчит обе
формулировки одинаково.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.classifier import classify


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.from_user.id = 42
    m.chat.id = 1
    m.text = text
    m.answer = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_classify_one_time_expense_single():
    res = await classify("разовый расход билет в питер 15000")
    assert res == [{"type": "one_time_expense", "text": "разовый расход билет в питер 15000"}]


@pytest.mark.asyncio
async def test_classify_one_time_expense_compound():
    res = await classify("разовые: доверенность риэлтору 3500, налоги 8500")
    assert res[0]["type"] == "one_time_expense"


@pytest.mark.asyncio
async def test_classify_one_time_expense_not_memory_save():
    res = await classify("разовый расход справка 2000")
    assert res[0]["type"] != "memory_save"


@pytest.mark.asyncio
async def test_classify_permanent_and_obligatory_both_memory_save():
    """Переименование: обе формулировки → memory_save (обратная совместимость)."""
    a = await classify("постоянный расход квартира 25000")
    b = await classify("обязательный расход квартира 25000")
    assert a[0]["type"] == "memory_save"
    assert b[0]["type"] == "memory_save"


@pytest.mark.asyncio
async def test_handle_one_time_expense_writes_finance_not_memory():
    from nexus.handlers import finance

    fake_haiku = AsyncMock(return_value=(
        '{"items": [{"description": "билет в питер", "amount": 15000, '
        '"category": "🚕 Транспорт"}]}'
    ))
    with patch.object(finance, "ask_claude", fake_haiku), \
         patch.object(finance, "_save_finance", AsyncMock(return_value="pg-1")) as m_fin, \
         patch("core.repos.memory_repo._repo.add", AsyncMock()) as m_mem_add, \
         patch("core.repos.memory_repo._repo.upsert", AsyncMock()) as m_mem_up:
        msg = _msg("разовый расход билет в питер 15000")
        await finance.handle_one_time_expense(msg, msg.text, user_notion_id="u-1")

    m_fin.assert_awaited_once()
    payload = m_fin.call_args.args[0]
    assert payload["amount"] == 15000.0
    assert payload["type_"] == "💸 Расход"
    assert payload["category"] == "🚕 Транспорт"
    m_mem_add.assert_not_awaited()
    m_mem_up.assert_not_awaited()
    msg.answer.assert_awaited_once()
    out = msg.answer.call_args.args[0]
    assert out.startswith("📤 Разовые: билет в питер — 15,000₽")
    assert "учтётся только в этом периоде" in out
    assert "расход" not in out  # однословное название, как у Постоянные/Долги/Цели


@pytest.mark.asyncio
async def test_handle_one_time_expense_compound_writes_n_transactions():
    from nexus.handlers import finance

    fake_haiku = AsyncMock(return_value=(
        '{"items": [{"description": "доверенность", "amount": 3500, "category": "💳 Прочее"}, '
        '{"description": "налоги", "amount": 8500, "category": "💳 Прочее"}]}'
    ))
    with patch.object(finance, "ask_claude", fake_haiku), \
         patch.object(finance, "_save_finance", AsyncMock(return_value="pg-x")) as m_fin:
        msg = _msg("разовые: доверенность 3500, налоги 8500")
        await finance.handle_one_time_expense(msg, msg.text, user_notion_id="u-1")

    assert m_fin.await_count == 2
    out = msg.answer.call_args.args[0]
    assert out.startswith("📤 Разовые — 12,000₽")


@pytest.mark.asyncio
async def test_handle_one_time_expense_no_amount_asks_again():
    from nexus.handlers import finance

    with patch.object(finance, "ask_claude", AsyncMock(return_value='{"items": []}')), \
         patch.object(finance, "_save_finance", AsyncMock()) as m_fin:
        msg = _msg("разовый расход хрень")
        await finance.handle_one_time_expense(msg, msg.text, user_notion_id="u-1")

    m_fin.assert_not_awaited()
    assert "Не понял" in msg.answer.call_args.args[0]
