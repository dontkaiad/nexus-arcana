"""tests/test_subtask_confirmation_registers_reply.py — создание подзадач/
чеклиста регистрирует confirmation-сообщение в message_pages (#256), чтобы
reply "добавь ещё пункты" на него нашёл дорогу в core.reply_update._apply_checklist
вместо обычного classify() (см. tests/test_checklist_reply_add_items.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.repos.lists_repo import pending_set
import nexus.handlers.lists as lists_mod


def _mk_msg(uid: int, text: str):
    msg = MagicMock()
    msg.from_user.id = uid
    msg.text = text
    msg.chat.id = uid
    sent = MagicMock()
    sent.chat.id = uid
    sent.message_id = 555
    msg.answer = AsyncMock(return_value=sent)
    return msg, sent


@pytest.mark.asyncio
async def test_subtask_items_registers_message_page():
    uid = 4242
    pending_set(uid, {
        "action": "subtask_items", "task_id": "93", "task_name": "Подготовиться к поездке",
        "rel_type": "task", "user_id": "u1", "bot": "nexus",
    })
    msg, sent = _mk_msg(uid, "пункт А\nпункт Б")

    with patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "_repo") as m_repo, \
         patch("core.message_pages.save_message_page", AsyncMock()) as m_save:
        m_repo.add = AsyncMock(return_value=[
            {"id": "1", "name": "пункт А"}, {"id": "2", "name": "пункт Б"},
        ])
        handled = await lists_mod.handle_list_pending(msg, user_id="u1")

    assert handled is True
    m_save.assert_awaited_once_with(
        chat_id=uid, message_id=555, page_id="task:93", page_type="checklist", bot="nexus",
    )


@pytest.mark.asyncio
async def test_checklist_items_registers_message_page():
    uid = 4243
    pending_set(uid, {"action": "checklist_items", "group": "Список дел", "user_id": "u1"})
    msg, sent = _mk_msg(uid, "пункт А")

    with patch.object(lists_mod, "react", AsyncMock()), \
         patch.object(lists_mod, "_repo") as m_repo, \
         patch("core.message_pages.save_message_page", AsyncMock()) as m_save:
        m_repo.add = AsyncMock(return_value=[{"id": "1", "name": "пункт А"}])
        m_repo.add_checklist_task = AsyncMock(return_value="77")
        handled = await lists_mod.handle_list_pending(msg, user_id="u1")

    assert handled is True
    m_save.assert_awaited_once_with(
        chat_id=uid, message_id=555, page_id="task:77", page_type="checklist", bot="nexus",
    )
