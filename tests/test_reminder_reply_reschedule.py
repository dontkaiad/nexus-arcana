"""tests/test_reminder_reply_reschedule.py — #170: reply на плашку
напоминания = перенос + anti-loop guard на перенос в прошлое.

Баг: reply «напомни в 19» на плашку «🔔 Напоминание: …» уходил в общий
классификатор («🤔 Не понял»), а перенос в прошлое срабатывал мгновенно
→ мёртвая петля. Фикс:
- reverse-lookup плашки по (chat_id, message_id) → reschedule-пайплайн;
- _is_future_dt guard: прошлое → переспросить, pending не теряем.

Privacy: generic названия задач, синтетические id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── reverse-lookup плашки ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task_reminder_by_message_roundtrip():
    from core import task_reminder_msg as trm

    chat_id, message_id, task_id = 770_001, 555_111, "task-170-a"
    await trm.save_task_reminder(task_id, chat_id, message_id, "разобрать почту")
    try:
        found = await trm.get_task_reminder_by_message(chat_id, message_id)
        assert found is not None
        assert found["task_id"] == task_id
        assert found["title"] == "разобрать почту"

        # Чужой message_id → None
        assert await trm.get_task_reminder_by_message(chat_id, 999_999) is None
    finally:
        await trm.delete_task_reminder(task_id)

    # После удаления плашки lookup пустой
    assert await trm.get_task_reminder_by_message(chat_id, message_id) is None


# ── anti-loop guard ───────────────────────────────────────────────────────────


def test_is_future_dt():
    from nexus.handlers.tasks import _is_future_dt

    tz = 3
    now = datetime.now(timezone(timedelta(hours=tz)))
    past = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    future = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

    assert _is_future_dt(future, tz) is True
    assert _is_future_dt(past, tz) is False


def _msg(uid: int = 770_002):
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = uid
    m.chat = MagicMock()
    m.chat.id = uid
    m.text = "напомни в 19"
    m.answer = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_reschedule_rejects_past_keeps_pending():
    """Перенос в прошлое → «уже прошло», _schedule_reminder НЕ зовётся,
    pending сохраняется (петля не возникает)."""
    from nexus.handlers import tasks

    uid = 770_003
    msg = _msg(uid)
    now = datetime.now(timezone(timedelta(hours=3)))
    past = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")

    tasks._pending_set(uid, {"task_id": "t1", "action": "reschedule", "title": "почта"})
    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks, "ask_claude", AsyncMock(return_value='{"reminder_time": "%s"}' % past)), \
             patch.object(tasks, "_schedule_reminder", AsyncMock()) as sched, \
             patch.object(tasks, "_update_notion_on_reschedule", AsyncMock()), \
             patch.object(tasks, "react", AsyncMock()):
            await tasks.handle_reschedule_reminder(msg)

        sched.assert_not_called()
        assert "прошл" in msg.answer.call_args[0][0].lower()
        # pending не удалён — следующее сообщение повторит попытку
        assert tasks._pending_get(uid) is not None
    finally:
        tasks._pending_del(uid)


@pytest.mark.asyncio
async def test_reply_reschedule_future_schedules_and_clears_pending():
    """handle_reminder_reply_reschedule: future время → перенос +
    pending очищен."""
    from nexus.handlers import tasks

    uid = 770_004
    msg = _msg(uid)
    now = datetime.now(timezone(timedelta(hours=3)))
    future = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

    tasks._pending_del(uid)
    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks, "ask_claude", AsyncMock(return_value='{"reminder_time": "%s"}' % future)), \
             patch.object(tasks, "_schedule_reminder", AsyncMock()) as sched, \
             patch.object(tasks, "_update_notion_on_reschedule", AsyncMock()) as notion, \
             patch.object(tasks, "react", AsyncMock()):
            await tasks.handle_reminder_reply_reschedule(msg, "t2", "почта")

        sched.assert_awaited_once()
        notion.assert_awaited_once()
        assert "перенесено" in msg.answer.call_args[0][0].lower()
        # pending очищен после успешного переноса
        assert tasks._pending_get(uid) is None
    finally:
        tasks._pending_del(uid)
