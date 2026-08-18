"""Regression: reply "напоминание завтра в 11" on a task confirmation message
routed the date into deadline instead of reminder, because the task reply-update
schema (core/reply_update.py) only ever had a deadline field — no reminder field
existed at all, so Haiku had nowhere else to put a reminder date.

Repro (user report): task created with deadline only (no "напомни" in the
original text). Reply "напоминание завтра в 11" → bot answered
"Дополнено: • Дедлайн: <same date>" instead of setting Напоминание.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.reply_update as ru


def test_task_reply_system_has_reminder_field_and_word_rule():
    system = ru._task_reply_system(3)
    assert '"reminder"' in system
    assert "напомин" in system.lower()


@pytest.mark.asyncio
async def test_task_reply_calls_pg_tasks_set_props_with_reminder():
    from nexus.repos.pg_tasks_repo import PgTasksRepo
    with patch.object(PgTasksRepo, "set_props", AsyncMock()) as m:
        applied = await ru.apply_updates(
            "42", "task", None, {"reminder": "2026-08-19 11:00"},
        )
    m.assert_awaited_once()
    page_id, props = m.await_args.args
    assert page_id == "42"
    assert props["Напоминание"] == {"date": {"start": "2026-08-19T11:00"}}
    assert "Дедлайн" not in props
    assert applied["Напоминание"] == "2026-08-19T11:00"


@pytest.mark.asyncio
async def test_reply_напоминание_reschedules_reminder_job_not_deadline():
    """End-to-end: parse_reply routes to reminder, apply_updates writes
    Напоминание, and the handler reschedules the reminder job (not the
    deadline job) using the live APScheduler path."""
    from nexus.handlers.reply_update import handle_reply_update

    reply_msg = MagicMock()
    reply_msg.reply_to_message = MagicMock()
    reply_msg.reply_to_message.from_user = MagicMock(is_bot=True)
    reply_msg.reply_to_message.message_id = 999
    reply_msg.chat = MagicMock(id=111)
    reply_msg.text = "напоминание завтра в 11"
    reply_msg.caption = None
    reply_msg.from_user = MagicMock(id=67686090)
    reply_msg.answer = AsyncMock()

    mapping = {"page_id": "pgtask-1", "page_type": "task", "bot": "nexus"}
    task = MagicMock(title="позвонить нотариусу")

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.reply_update.parse_reply",
               AsyncMock(return_value={"reminder": "2026-08-19T11:00"})), \
         patch("nexus.handlers.reply_update.apply_updates",
               AsyncMock(return_value={"Напоминание": "2026-08-19T11:00"})), \
         patch("nexus.handlers.reply_update.format_applied",
               AsyncMock(return_value="Напоминание: 2026-08-19T11:00")), \
         patch("nexus.repos.tasks_repo._repo.retrieve_page", AsyncMock(return_value=task)), \
         patch("nexus.handlers.tasks._schedule_reminder", AsyncMock()) as m_rem, \
         patch("nexus.handlers.tasks._schedule_deadline_check", AsyncMock()) as m_dl:
        handled = await handle_reply_update(reply_msg, user_notion_id="u")

    assert handled is True
    m_rem.assert_awaited_once_with(111, "позвонить нотариусу", "2026-08-19T11:00", "pgtask-1", 3)
    m_dl.assert_not_awaited()
    assert "Дедлайн" not in reply_msg.answer.await_args.args[0]
