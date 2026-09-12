"""tests/test_finance_income_marker_auto_resolve.py

Живой доход/расход-clarify идёт через core/classifier.py:process_item:
низкая уверенность + нет маркеров дохода/бартера/ambiguous → авто-расход;
иначе `finance_clarify:` (кнопки [Расход]/[Доход]/[Бартер] в nexus_bot).
Явный маркер («доход X») Haiku и так возвращает confidence=high (см. prompt),
до clarify не доходит.

(#208: старый handle_finance_text + его _pending_finance удалены — были
test-only и перебивались handle_text на уровне dp.)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.classifier as clf


def _msg(uid: int = 7):
    m = MagicMock()
    m.from_user.id = uid
    m.answer = AsyncMock()
    return m


def _income_data(amount, title, category="💳 Прочее", confidence="low"):
    return {
        "type": "income", "amount": amount, "title": title,
        "category": category, "source": "💳 Карта", "confidence": confidence,
    }


async def _run(data, text):
    msg = _msg()
    with patch.object(clf, "_fin_repo") as m_repo, \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.finance.handle_windfall_income", AsyncMock()), \
         patch("nexus.handlers.finance._check_budget_limit", AsyncMock()):
        m_repo.add = AsyncMock(return_value="page-1")
        line = await clf.process_item(data, text, msg, {}, user_id="u-1")
    return line, msg, m_repo


@pytest.mark.asyncio
async def test_ambiguous_word_alone_asks_buttons():
    """«аренда 5000» — ambiguous без контекста получения → finance_clarify."""
    line, msg, m_repo = await _run(
        _income_data(5000, "аренда", confidence="low"), "аренда 5000")
    assert line.startswith("finance_clarify:")
    m_repo.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_barter_marker_asks_buttons():
    line, _, m_repo = await _run(
        _income_data(3000, "обмен на дрель", confidence="low"),
        "бартер 3000 обмен на дрель")
    assert line.startswith("finance_clarify:")
    m_repo.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_markers_auto_expense():
    """«3000 инструменты» — ни дохода, ни бартера, ни ambiguous → авто-расход."""
    line, _, m_repo = await _run(
        _income_data(3000, "инструменты", confidence="low"), "3000 инструменты")
    assert not line.startswith("finance_clarify:")
    m_repo.add.assert_awaited_once()
    assert m_repo.add.await_args.kwargs["type_"] == "💸 Расход"


@pytest.mark.asyncio
async def test_zero_amount_offers_list_cross_off():
    """«купила сигареты энергетики и хлеб» без цены → amount=0, expense не
    пишем (нечего), но раньше это молча проглатывалось (только реакция) —
    теперь предлагаем вычеркнуть совпавшие позиции из 🛒 Покупки."""
    msg = _msg()
    data = {
        "type": "expense", "amount": 0, "title": "сигареты энергетики хлеб",
        "category": "💳 Прочее", "source": "💳 Карта", "confidence": "high",
    }
    matches = [
        {"id": "item-1", "name": "сигареты", "category": "🛒 Покупки"},
        {"id": "item-2", "name": "хлеб", "category": "🛒 Покупки"},
    ]
    with patch.object(clf, "_fin_repo") as m_repo, \
         patch.object(clf, "react", AsyncMock()), \
         patch("core.list_manager.find_matching_items", AsyncMock(return_value=matches)) as m_find:
        m_repo.add = AsyncMock(return_value="page-1")
        line = await clf.process_item(
            data, "купила сигареты энергетики и хлеб", msg, {}, user_id="u-1")
    m_repo.add.assert_not_awaited()
    m_find.assert_awaited_once()
    assert m_find.await_args.args[0] == "купила сигареты энергетики и хлеб"
    assert line == ""
    msg.answer.assert_awaited_once()
    sent_text = msg.answer.await_args.args[0]
    assert "сигареты" in sent_text and "хлеб" in sent_text


@pytest.mark.asyncio
async def test_zero_amount_no_list_match_stays_silent():
    """Без совпадений в списке покупок — как раньше, ничего не пишем."""
    msg = _msg()
    data = {
        "type": "expense", "amount": 0, "title": "погуляла",
        "category": "💳 Прочее", "source": "💳 Карта", "confidence": "high",
    }
    with patch.object(clf, "_fin_repo") as m_repo, \
         patch.object(clf, "react", AsyncMock()), \
         patch("core.list_manager.find_matching_items", AsyncMock(return_value=[])):
        m_repo.add = AsyncMock(return_value="page-1")
        line = await clf.process_item(data, "погуляла", msg, {}, user_id="u-1")
    m_repo.add.assert_not_awaited()
    msg.answer.assert_not_awaited()
    assert line == ""


@pytest.mark.asyncio
async def test_explicit_income_high_confidence_saved_as_income():
    """Haiku вернул confidence=high для «доход 3000» → сразу income, windfall-путь."""
    msg = _msg()
    data = _income_data(3000, "продала инструменты", category="🎁 Подарок", confidence="high")
    with patch.object(clf, "_fin_repo") as m_repo, \
         patch.object(clf, "react", AsyncMock()), \
         patch("nexus.handlers.finance.handle_windfall_income", AsyncMock()) as m_wf:
        m_repo.add = AsyncMock(return_value="page-1")
        line = await clf.process_item(data, "доход 3000 продала инструменты", msg, {}, user_id="u-1")
    m_repo.add.assert_awaited_once()
    assert m_repo.add.await_args.kwargs["type_"] == "💰 Доход"
    m_wf.assert_awaited_once()
    assert "₽" in line
