"""tests/test_finance_income_marker_auto_resolve.py

Явный маркер дохода в тексте (_INCOME_MARKERS_RE: доход/получила/зарплата/
перевели/вернули/пришло/поступил/аванс) без ambiguous-слов (_AMBIGUOUS_RE:
аренда/займ/долг) и без бартера (_BARTER_MARKERS_RE) → автоматически
резолвится как доход, кнопки "Доход/Расход/Бартер" не показываются.

Смешанные сигналы (доход + ambiguous, или что угодно + бартер) — по-прежнему
уточняем кнопками, регресс не ломаем.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import finance


def _msg(uid: int = 7):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()
    return msg


def _low_confidence_data(amount, description, type_="💸 Расход"):
    """Имитирует ответ Haiku-парсера с низкой уверенностью — как для текста
    без явного глагола расхода/дохода, где handle_finance_text решает сам."""
    return {
        "amount": amount,
        "description": description,
        "type_": type_,
        "category": "💳 Прочее",
        "source": "💳 Карта",
        "confidence": "low",
        "question": "Это доход или расход?",
    }


@pytest.mark.asyncio
async def test_explicit_income_marker_auto_resolves_no_buttons():
    """'доход 3000 продала инструменты' → авто-доход, кнопки не показываются."""
    text = "доход 3000 продала инструменты"
    data = _low_confidence_data(3000, "продала инструменты")

    save_mock = AsyncMock(return_value="page-1")
    with patch.object(finance, "ask_claude", AsyncMock(return_value=json.dumps(data, ensure_ascii=False))), \
         patch.object(finance, "_save_finance", save_mock), \
         patch.object(finance, "react", AsyncMock()):
        msg = _msg()
        await finance.handle_finance_text(msg, text, user_notion_id="u-1")

    # Никаких кнопок — сохранили сразу, одно сообщение с записью.
    for call in msg.answer.call_args_list:
        assert "reply_markup" not in call.kwargs
        assert "доход или расход" not in call.args[0].lower() if call.args else True

    saved_data = save_mock.call_args.args[0]
    assert saved_data["type_"] == "💰 Доход"
    assert saved_data["confidence"] == "high"


@pytest.mark.asyncio
async def test_ambiguous_word_alone_still_asks_buttons():
    """'аренда 5000' — амбивалентно (аренда без контекста получения) →
    по-прежнему уточняем кнопками. Регресс не ломаем."""
    text = "аренда 5000"
    data = _low_confidence_data(5000, "аренда")

    with patch.object(finance, "ask_claude", AsyncMock(return_value=json.dumps(data, ensure_ascii=False))):
        msg = _msg()
        await finance.handle_finance_text(msg, text, user_notion_id="u-1")

    assert finance._pending_finance.get(msg.from_user.id) is not None
    last_call = msg.answer.call_args_list[-1]
    assert "reply_markup" in last_call.kwargs
    finance._pending_finance.pop(msg.from_user.id, None)


@pytest.mark.asyncio
async def test_income_marker_mixed_with_ambiguous_still_asks_buttons():
    """'доход 3000, сдаю в аренду' — и доход, и ambiguous разом →
    смешанные сигналы, не авто-резолвим, показываем кнопки."""
    text = "доход 3000, сдаю в аренду"
    data = _low_confidence_data(3000, "сдаю в аренду")

    with patch.object(finance, "ask_claude", AsyncMock(return_value=json.dumps(data, ensure_ascii=False))):
        msg = _msg()
        await finance.handle_finance_text(msg, text, user_notion_id="u-1")

    last_call = msg.answer.call_args_list[-1]
    assert "reply_markup" in last_call.kwargs
    finance._pending_finance.pop(msg.from_user.id, None)


@pytest.mark.asyncio
async def test_barter_marker_still_asks_buttons():
    """'бартер 3000 обмен на расклад' — barter-путь не трогаем, кнопки как раньше."""
    text = "бартер 3000 обмен на расклад"
    data = _low_confidence_data(3000, "обмен на расклад")

    with patch.object(finance, "ask_claude", AsyncMock(return_value=json.dumps(data, ensure_ascii=False))):
        msg = _msg()
        await finance.handle_finance_text(msg, text, user_notion_id="u-1")

    last_call = msg.answer.call_args_list[-1]
    assert "reply_markup" in last_call.kwargs
    finance._pending_finance.pop(msg.from_user.id, None)


@pytest.mark.asyncio
async def test_no_markers_at_all_auto_expense_regression():
    """'3000 инструменты' — без всяких маркеров → авто-расход, как раньше."""
    text = "3000 инструменты"
    data = _low_confidence_data(3000, "инструменты")

    save_mock = AsyncMock(return_value="page-1")
    with patch.object(finance, "ask_claude", AsyncMock(return_value=json.dumps(data, ensure_ascii=False))), \
         patch.object(finance, "_save_finance", save_mock), \
         patch.object(finance, "react", AsyncMock()), \
         patch.object(finance, "_check_budget_limit", AsyncMock()):
        msg = _msg()
        await finance.handle_finance_text(msg, text, user_notion_id="u-1")

    saved_data = save_mock.call_args.args[0]
    assert saved_data["type_"] == "💸 Расход"
    assert saved_data["confidence"] == "high"
