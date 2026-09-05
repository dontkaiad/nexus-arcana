"""tests/test_subtask_group_complete.py — авто-завершение подзадач должно
реально проставлять статус родителя (Nexus ✅ Задача / Arcana 🔮 Работа), а не
только слать поздравление.

Баг (см. docs/CASES/AUDIT_works_vs_tasks_parity.md): при чеке последней
подзадачи чеклиста бот показывал «🎉 Все подзадачи готовы!», но статус
родительской записи в БД не менялся.

- Nexus (`nexus/handlers/lists.py` on_checkout → on_complete_task) уже
  проставлял статус правильно — тест здесь регрессионный, фиксирует что
  реально работающий путь не сломан.
- Arcana (`arcana/handlers/lists.py` on_checkout) group-complete вообще не
  детектила и work_id никогда не получал шанс закрыться — это и есть фикс.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import nexus.handlers.lists as nx
import arcana.handlers.lists as ar
from core.repos.lists_repo import pending_set, pending_del


def _fake_query(data: str, uid: int = 555) -> MagicMock:
    q = MagicMock()
    q.data = data
    q.from_user.id = uid
    q.message = MagicMock()
    q.message.answer = AsyncMock()
    q.message.reply = AsyncMock()
    q.message.edit_reply_markup = AsyncMock()
    q.answer = AsyncMock()
    return q


# ── Nexus: ✅ Задачи ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nexus_checkout_offers_complete_task_on_group_done():
    uid = 5551
    pending_set(uid, {
        "action": "list_select",
        "selected": ["101"],
        "list_type": None,
        "user_notion_id": "u1",
    })
    item = {
        "id": "101", "name": "Зажечь свечу", "type": "📋 Чеклист",
        "group": "Ритуал для Маши", "task_rel": "42", "status": "Not started",
    }
    q = _fake_query("lt_checkout", uid)

    with patch.object(nx, "_fetch_all_display_items", AsyncMock(return_value=[item])), \
         patch.object(nx._repo, "mark_done", AsyncMock(return_value=1)):
        await nx.on_checkout(q, user_notion_id="u1")

    texts = [c.args[0] for c in q.message.answer.await_args_list]
    assert any("🎉 Все подзадачи «Ритуал для Маши» готовы!" in t for t in texts)

    confirm_calls = [c for c in q.message.answer.await_args_list
                     if "Завершить задачу" in c.args[0]]
    assert confirm_calls, "должна предложить завершить родительскую задачу"
    kb = confirm_calls[0].kwargs.get("reply_markup")
    cb_data = kb.inline_keyboard[0][0].callback_data
    assert cb_data == "list_complete_task_42"

    pending_del(uid)


@pytest.mark.asyncio
async def test_nexus_on_complete_task_marks_status_done():
    """Тап по «✅ Завершить задачу» реально проставляет Done (регрессия)."""
    q = _fake_query("list_complete_task_42")

    with patch.object(nx._repo, "find_task", AsyncMock(return_value=[])), \
         patch.object(nx._repo, "mark_task_done", AsyncMock(return_value=True)) as mock_done:
        await nx.on_complete_task(q, user_notion_id="u1")

    mock_done.assert_awaited_once_with("42")
    q.message.reply.assert_awaited_once()
    assert "выполненная" in q.message.reply.await_args.args[0]


# ── Arcana: 🔮 Работы ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_arcana_checkout_offers_complete_work_on_group_done():
    uid = 5552
    pending_set(uid, {
        "action": "list_select",
        "selected": ["201"],
        "list_type": None,
        "user_notion_id": "u2",
    })
    item = {
        "id": "201", "name": "Зажечь свечу", "type": "📋 Чеклист",
        "group": "Ритуал для Маши", "work_rel": "77", "status": "Not started",
    }
    q = _fake_query("lt_checkout", uid)

    with patch.object(ar, "_fetch_all_display_items", AsyncMock(return_value=[item])), \
         patch.object(ar._repo, "mark_done", AsyncMock(return_value=1)):
        await ar.on_checkout(q, user_notion_id="u2")

    texts = [c.args[0] for c in q.message.answer.await_args_list]
    assert any("🎉 Все подзадачи «Ритуал для Маши» готовы!" in t for t in texts), (
        "group-complete должен детектиться и у Работ, не только у Задач"
    )

    confirm_calls = [c for c in q.message.answer.await_args_list
                     if "Завершить работу" in c.args[0]]
    assert confirm_calls, "должна предложить завершить родительскую работу"
    kb = confirm_calls[0].kwargs.get("reply_markup")
    cb_data = kb.inline_keyboard[0][0].callback_data
    assert cb_data == "list_complete_work_77"

    pending_del(uid)


@pytest.mark.asyncio
async def test_arcana_on_complete_work_marks_status_done():
    """Тап по «✅ Завершить работу» реально проставляет Done (был баг — не было вообще)."""
    q = _fake_query("list_complete_work_77")

    with patch.object(ar._repo, "mark_work_done", AsyncMock(return_value=True)) as mock_done:
        await ar.on_complete_work(q, user_notion_id="u2")

    mock_done.assert_awaited_once_with("77")
    q.message.reply.assert_awaited_once()
    assert "выполненная" in q.message.reply.await_args.args[0]
