"""tests/test_reschedule_cancel_intent.py

Баг: после «❌ Не сделал» бот ждёт дату («Когда напомнить снова?»). Если
вместо даты написать «отмени задачу», это уходит прямиком в date-парсер
(regex + Haiku), парсинг проваливается → «Не смог парсить дату. Попробуй
ещё раз» — бот никогда не распознаёт намерение отменить задачу, хотя
`handle_task_cancel` для этого существует (просто недостижим из pending
reschedule-состояния).

Фикс: `handle_reschedule_reminder` проверяет cancel-intent слова ДО
попытки распарсить дату и архивирует уже известный `task_id` напрямую.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _msg(uid: int, text: str):
    m = MagicMock()
    m.from_user = MagicMock()
    m.from_user.id = uid
    m.chat = MagicMock()
    m.chat.id = uid
    m.text = text
    m.answer = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_cancel_intent_archives_task_without_trying_to_parse_date():
    from nexus.handlers import tasks

    uid = 770_201
    msg = _msg(uid, "отмени задачу")
    tasks._pending_set(uid, {"task_id": "t-cancel-1", "action": "reschedule", "title": "заказать доставку"})

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks._repo, "set_archived", AsyncMock()) as archived, \
             patch.object(tasks, "ask_claude", AsyncMock()) as haiku, \
             patch.object(tasks, "react", AsyncMock()) as react_mock:
            await tasks.handle_reschedule_reminder(msg)

        archived.assert_awaited_once_with("t-cancel-1")
        haiku.assert_not_awaited()
        react_mock.assert_awaited_once_with(msg, "💔")
        assert "отменена" in msg.answer.call_args[0][0].lower()
        # pending очищен — следующее сообщение не попадёт снова в reschedule
        assert tasks._pending_get(uid) is None
    finally:
        tasks._pending_del(uid)


@pytest.mark.asyncio
async def test_cancel_intent_recognized_via_maybe_handle_pending_entrypoint():
    """Тот же сценарий через единую точку входа (как реально роутится текст)."""
    from nexus.handlers import tasks

    uid = 770_202
    msg = _msg(uid, "отмени")
    tasks._pending_set(uid, {"task_id": "t-cancel-2", "action": "reschedule", "title": "полить цветы"})

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks._repo, "set_archived", AsyncMock()) as archived, \
             patch.object(tasks, "ask_claude", AsyncMock()) as haiku, \
             patch.object(tasks, "react", AsyncMock()):
            handled = await tasks.maybe_handle_reschedule_pending(msg)

        assert handled is True
        archived.assert_awaited_once_with("t-cancel-2")
        haiku.assert_not_awaited()
    finally:
        tasks._pending_del(uid)


def test_is_cancel_intent_matches_common_phrasings():
    from nexus.handlers.tasks import _is_cancel_intent

    assert _is_cancel_intent("отмени задачу") is True
    assert _is_cancel_intent("отменить") is True
    assert _is_cancel_intent("удали её") is True
    assert _is_cancel_intent("cancel") is True

    # Обычные ответы с датой не должны считаться отменой
    assert _is_cancel_intent("завтра в 10:00") is False
    assert _is_cancel_intent("через 2 часа") is False
    assert _is_cancel_intent("в понедельник") is False
