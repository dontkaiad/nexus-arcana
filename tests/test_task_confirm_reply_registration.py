"""tests/test_task_confirm_reply_registration.py — reply-broken bug.

Repro: create a task, bot answers "⚡ Задача создана!", reply to that
message with "перенеси на среду" → bot answered {"type":"unknown"}.

Root cause: _do_save_task never registered the confirmation message in
message_pages, unlike the equivalent Arcana flows (clients/sessions/works/
rituals all call save_message_page after their creation confirmation). So
handle_reply_update had no (chat_id, message_id) → page_id mapping to look
up, the reply fell through to the generic classify(), and Haiku had no
"task" context to make sense of "перенеси на среду" → unknown.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_do_save_task_registers_confirm_message_for_reply(mock_message):
    import nexus.handlers.tasks as tasks_mod
    import nexus.repos.pg_tasks_repo as pg_tasks_repo

    msg = mock_message(text="дождаться ответа по 3 этапу")
    data = {
        "title": "дождаться ответа по 3 этапу",
        "category": "💳 Прочее",
        "priority": "Важно",
        "deadline": None,
        "repeat": "Нет",
    }

    with patch.object(tasks_mod._repo, "create", AsyncMock(return_value="pgtask-1")), \
         patch.object(pg_tasks_repo, "_ensure_lookups", MagicMock()), \
         patch.object(tasks_mod, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks_mod, "ask_claude", AsyncMock(return_value="")), \
         patch("core.message_pages.save_message_page", AsyncMock()) as m_smp:
        await tasks_mod._do_save_task(msg, data, chat_id=msg.chat.id, uid=msg.from_user.id)

    m_smp.assert_awaited_once()
    kwargs = m_smp.await_args.kwargs
    assert kwargs["page_id"] == "pgtask-1"
    assert kwargs["page_type"] == "task"
    assert kwargs["bot"] == "nexus"
    assert kwargs["chat_id"] == msg.chat.id
    assert kwargs["message_id"] == msg.answer.return_value.message_id


@pytest.mark.asyncio
async def test_reply_to_confirm_message_routes_to_task_update():
    """End-to-end of the fix: once message_pages knows the confirmation msg,
    handle_reply_update resolves it and calls the task-update pipeline
    instead of leaving the reply unhandled."""
    from nexus.handlers.reply_update import handle_reply_update

    reply_msg = MagicMock()
    reply_msg.reply_to_message = MagicMock()
    reply_msg.reply_to_message.text = "⚡ Задача создана!\n📌 дождаться ответа"
    reply_msg.reply_to_message.from_user = MagicMock(is_bot=True)
    reply_msg.reply_to_message.message_id = 999
    reply_msg.chat = MagicMock(id=111)
    reply_msg.text = "перенеси на среду"
    reply_msg.caption = None
    reply_msg.from_user = MagicMock(id=67686090)
    reply_msg.answer = AsyncMock()

    mapping = {"page_id": "pgtask-1", "page_type": "task", "bot": "nexus"}

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.reply_update.parse_reply",
               AsyncMock(return_value={"deadline": "2026-08-19"})) as m_parse, \
         patch("nexus.handlers.reply_update.apply_updates",
               AsyncMock(return_value={"Дедлайн": "2026-08-19"})), \
         patch("nexus.handlers.reply_update.format_applied", AsyncMock(return_value="Дедлайн: 2026-08-19")):
        handled = await handle_reply_update(reply_msg, user_notion_id="u")

    assert handled is True
    m_parse.assert_awaited_once()
    assert m_parse.await_args.args[0] == "task"
    assert m_parse.await_args.args[1] == "перенеси на среду"
    assert m_parse.await_args.kwargs["tz_offset"] == 3
    reply_msg.answer.assert_awaited_once()
    assert "unknown" not in reply_msg.answer.await_args.args[0].lower()
