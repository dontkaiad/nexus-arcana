"""tests/test_checklist_reply_add_items.py — reply "добавь ещё пункты" на
подтверждение подзадач/чеклиста дописывает НОВЫЕ пункты в тот же чеклист
(#256, по образцу #192 test_list_reply_add_item.py).

Раньше это вообще не работало: подтверждение подзадач/чеклиста не
регистрировалось в message_pages, поэтому reply падал в обычный classify()
как новое сообщение — многострочный текст создавал отдельные новые задачи
вместо дописывания в существующий чеклист (не баг в узком смысле, а
незаложенная логика).

Privacy: generic placeholder-пункты, никаких реальных данных.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.reply_update import _apply_checklist


def _mk_reply_message(reply_text: str, chat_id: int = 1, orig_msg_id: int = 10):
    orig = MagicMock()
    orig.text = "📋 <b>Подготовиться к поездке</b> — 3 подзадач:\n  ⬜ пункт А"
    orig.caption = None
    orig.message_id = orig_msg_id

    msg = MagicMock()
    msg.reply_to_message = orig
    msg.text = reply_text
    msg.caption = None
    msg.chat.id = chat_id
    msg.from_user.id = chat_id
    msg.answer = AsyncMock()
    return msg, orig


# ── _apply_checklist: unit-уровень ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_checklist_adds_multiple_items_to_same_group():
    existing = [SimpleNamespace(group_name="Подготовиться к поездке", user_id="u1")]
    with patch("core.list_manager._nexus_repo.get_items_for_task", AsyncMock(return_value=existing)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(
             return_value=[{"id": "1", "name": "пункт Б"}, {"id": "2", "name": "пункт В"}])) as add:
        applied = await _apply_checklist("task:93", {"add_items": ["пункт Б", "пункт В"]})

    assert applied == {"пункт 1": "пункт Б", "пункт 2": "пункт В"}
    add.assert_awaited_once_with(
        [{"name": "пункт Б", "group": "Подготовиться к поездке", "task_rel": "93"},
         {"name": "пункт В", "group": "Подготовиться к поездке", "task_rel": "93"}],
        "📋 Чеклист", "☀️ Nexus", "u1",
    )


@pytest.mark.asyncio
async def test_apply_checklist_no_add_items_is_noop():
    with patch("core.list_manager._nexus_repo.get_items_for_task", AsyncMock()) as get_items:
        applied = await _apply_checklist("task:93", {})
    assert applied == {}
    get_items.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_checklist_unknown_task_returns_empty():
    with patch("core.list_manager._nexus_repo.get_items_for_task", AsyncMock(return_value=[])), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock()) as add:
        applied = await _apply_checklist("task:999", {"add_items": ["X"]})
    assert applied == {}
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_checklist_work_rel_type_not_supported_yet():
    """rel_type='work' намеренно не реализован (см. docstring _apply_checklist) —
    Arcana add_items() уводит bot_name='🌒 Arcana' в другой путь, не проверено."""
    with patch("core.list_manager._nexus_repo.get_items_for_task", AsyncMock()) as get_items:
        applied = await _apply_checklist("work:12", {"add_items": ["X"]})
    assert applied == {}
    get_items.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_checklist_add_failure_returns_empty():
    existing = [SimpleNamespace(group_name="Группа", user_id="u1")]
    with patch("core.list_manager._nexus_repo.get_items_for_task", AsyncMock(return_value=existing)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(return_value=[])):
        applied = await _apply_checklist("task:93", {"add_items": ["X"]})
    assert applied == {}


# ── handle_reply_update: интеграция с диспатчем ──────────────────────────────

@pytest.mark.asyncio
async def test_reply_add_items_end_to_end_confirms_to_user():
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("добавь ещё пункты: пункт Б, пункт В")
    mapping = {"page_id": "task:93", "page_type": "checklist", "bot": "nexus"}
    existing = [SimpleNamespace(group_name="Подготовиться к поездке", user_id="u1")]

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(
             return_value={"add_items": ["пункт Б", "пункт В"]})), \
         patch("core.list_manager._nexus_repo.get_items_for_task", AsyncMock(return_value=existing)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(
             return_value=[{"id": "1", "name": "пункт Б"}, {"id": "2", "name": "пункт В"}])), \
         patch("nexus.handlers.reply_update.react", AsyncMock()):
        handled = await ru.handle_reply_update(msg, user_id="u1")

    assert handled is True
    msg.answer.assert_any_call("✏️ Дополнено:\n  • пункт 1: пункт Б\n  • пункт 2: пункт В")
