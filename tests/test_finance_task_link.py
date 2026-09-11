"""tests/test_finance_task_link.py — транзакция↔задача линковка (#225).

"продала телевизор 4000" при наличии открытой задачи "продать телевизор
большой" должна предложить закрыть её, а не тихо записать доход отдельно.
Работает для expense И income (core/classifier.py, обе ветки общие).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import classifier as clf
from nexus.repos.pg_tasks_repo import Task as PgTask
from nexus.handlers.tasks import find_task_matches_for_finance, on_task_cross, on_task_cross_no


def _task(id_="t1", title="продать телевизор большой"):
    return PgTask(id=id_, title=title, user_id="uid")


# ── find_task_matches_for_finance ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finds_matching_task_by_shared_words():
    tasks = [_task("t1", "продать телевизор большой"), _task("t2", "продать телевизор на кухне")]
    with patch("nexus.handlers.tasks._repo.active", AsyncMock(return_value=tasks)):
        matches = await find_task_matches_for_finance("телевизор", "uid")
    assert {tid for tid, _ in matches} == {"t1", "t2"}


@pytest.mark.asyncio
async def test_no_match_returns_empty():
    tasks = [_task("t1", "помыть машину")]
    with patch("nexus.handlers.tasks._repo.active", AsyncMock(return_value=tasks)):
        matches = await find_task_matches_for_finance("продукты", "uid")
    assert matches == []


@pytest.mark.asyncio
async def test_no_active_tasks_returns_empty():
    with patch("nexus.handlers.tasks._repo.active", AsyncMock(return_value=[])):
        matches = await find_task_matches_for_finance("телевизор", "uid")
    assert matches == []


@pytest.mark.asyncio
async def test_limits_to_three_matches():
    tasks = [_task(f"t{i}", f"продать телевизор {i}") for i in range(5)]
    with patch("nexus.handlers.tasks._repo.active", AsyncMock(return_value=tasks)):
        matches = await find_task_matches_for_finance("телевизор", "uid")
    assert len(matches) == 3


# ── classifier wiring: expense/income → task_cross buttons ──────────────────

@pytest.fixture
def fake_msg():
    m = MagicMock()
    m.answer = AsyncMock()
    m.chat.id = 1
    m.from_user.id = 42
    return m


@pytest.mark.asyncio
async def test_income_offers_task_close(fake_msg):
    data = {"type": "income", "amount": 4000, "category": "💳 Прочее",
            "source": "💳 Карта", "title": "продала телевизор", "confidence": "high"}
    matches = [("t1", "продать телевизор большой")]
    with patch.object(clf._fin_repo, "add", AsyncMock(return_value="p")), \
         patch("nexus.handlers.finance.handle_windfall_income", AsyncMock()), \
         patch("nexus.handlers.tasks.find_task_matches_for_finance", AsyncMock(return_value=matches)):
        await clf.process_item(data, "продала телевизор 4000", fake_msg, {}, "uid")
    fake_msg.answer.assert_awaited()
    prompt_call = fake_msg.answer.call_args_list[-1]
    assert "продать телевизор большой" in prompt_call[0][0]
    kb = prompt_call[1]["reply_markup"].inline_keyboard
    assert kb[0][0].callback_data == "task_cross_t1"
    assert kb[-1][0].callback_data == "task_cross_no"


@pytest.mark.asyncio
async def test_expense_offers_task_close_too(fake_msg):
    data = {"type": "expense", "amount": 500, "category": "🍜 Продукты",
            "source": "💳 Карта", "title": "продукты", "confidence": "high"}
    matches = [("t9", "купить продукты на неделю")]
    with patch.object(clf._fin_repo, "add", AsyncMock(return_value="p")), \
         patch("nexus.handlers.finance._check_budget_limit", AsyncMock()), \
         patch("core.list_manager.find_matching_items", AsyncMock(return_value=[])), \
         patch("nexus.handlers.tasks.find_task_matches_for_finance", AsyncMock(return_value=matches)):
        await clf.process_item(data, "купила продукты 500", fake_msg, {}, "uid")
    texts = [c[0][0] for c in fake_msg.answer.call_args_list]
    assert any("купить продукты на неделю" in t for t in texts)


@pytest.mark.asyncio
async def test_no_match_no_extra_message(fake_msg):
    data = {"type": "expense", "amount": 500, "category": "🍜 Продукты",
            "source": "💳 Карта", "title": "продукты", "confidence": "high"}
    with patch.object(clf._fin_repo, "add", AsyncMock(return_value="p")), \
         patch("nexus.handlers.finance._check_budget_limit", AsyncMock()), \
         patch("core.list_manager.find_matching_items", AsyncMock(return_value=[])), \
         patch("nexus.handlers.tasks.find_task_matches_for_finance", AsyncMock(return_value=[])):
        await clf.process_item(data, "купила продукты 500", fake_msg, {}, "uid")
    fake_msg.answer.assert_not_awaited()  # только return-строка боту, доп. сообщений нет


@pytest.mark.asyncio
async def test_task_matching_failure_is_swallowed(fake_msg):
    """Сбой поиска задач (напр. БД недоступна) не должен ронять запись транзакции."""
    data = {"type": "income", "amount": 4000, "category": "💳 Прочее",
            "source": "💳 Карта", "title": "продала телевизор", "confidence": "high"}
    with patch.object(clf._fin_repo, "add", AsyncMock(return_value="p")), \
         patch("nexus.handlers.finance.handle_windfall_income", AsyncMock()), \
         patch("nexus.handlers.tasks.find_task_matches_for_finance",
               AsyncMock(side_effect=RuntimeError("db down"))):
        result = await clf.process_item(data, "продала телевизор 4000", fake_msg, {}, "uid")
    assert "4,000" in result or "4 000" in result or "4000" in result


# ── task_cross_ callbacks ─────────────────────────────────────────────────────

def _call(data):
    c = MagicMock()
    c.data = data
    c.from_user.id = 42
    c.answer = AsyncMock()
    c.message.edit_text = AsyncMock()
    c.message.edit_reply_markup = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_on_task_cross_closes_task_without_double_expense():
    task = _task("t1", "продать телевизор большой")
    c = _call("task_cross_t1")
    with patch("nexus.handlers.tasks._repo.retrieve_page", AsyncMock(return_value=task)), \
         patch("nexus.handlers.tasks._repo.set_status", AsyncMock(return_value=True)) as set_status, \
         patch("nexus.handlers.tasks._remove_task_jobs"), \
         patch("nexus.handlers.tasks._update_streak_line", AsyncMock(return_value="")), \
         patch("nexus.handlers.tasks._expense_from_note_on_done", AsyncMock()) as expense_hook:
        await on_task_cross(c)
    set_status.assert_awaited_once_with("t1", "Done")
    expense_hook.assert_not_awaited()  # деньги уже записаны классификатором — не задваиваем
    assert "закрыта" in c.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_on_task_cross_unknown_task():
    c = _call("task_cross_gone")
    with patch("nexus.handlers.tasks._repo.retrieve_page", AsyncMock(return_value=None)):
        await on_task_cross(c)
    c.answer.assert_awaited_once()
    assert "не найден" in c.answer.call_args[0][0] or "❓" in c.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_on_task_cross_no_clears_keyboard():
    c = _call("task_cross_no")
    await on_task_cross_no(c)
    c.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
