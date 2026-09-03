"""tests/test_reminder_clarify_day_of_month.py — «20 числа в 18 часов» в
5-минутном окне после создания задачи (`handle_last_task_clarify`).

Баг: `_CLARIFY_REMINDER_RE` — быстрый regex для «напомни в N» — не знает
про день месяца и хватает первое число после «напомни» как час. «напомни
20 числа в 18 часов» превращалось в час=20, «18» и «числа» терялись
(итог: напоминание на завтра в 20:00 вместо 20-го числа в 18:00).

Плюс: подтверждение из этой ветки не регистрировалось в
`task_reminder_msg`, поэтому reply-исправление на неверную плашку не
находило task_id и создавало отдельную новую задачу вместо правки.

Фикс:
- `_DAY_OF_MONTH_MARK_RE` гасит быстрый regex при «N числа» → фолбэк на
  Haiku через общий `_haiku_parse_reminder_dt` (тот же парсер, что и
  `handle_reschedule_reminder` — без дублирования).
- confirmation-сообщение регистрируется через `save_task_reminder`.

Privacy: синтетические id, generic названия задач.
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
    sent = MagicMock()
    sent.message_id = 424_242
    m.answer = AsyncMock(return_value=sent)
    return m, sent


# ── regex guard ──────────────────────────────────────────────────────────────

def test_day_of_month_marker_detected():
    from nexus.handlers.tasks import _DAY_OF_MONTH_MARK_RE

    assert _DAY_OF_MONTH_MARK_RE.search("напомни 20 числа в 18 часов")
    assert _DAY_OF_MONTH_MARK_RE.search("напомни 21-го числа в 9")
    assert not _DAY_OF_MONTH_MARK_RE.search("напомни в 18 часов")
    assert not _DAY_OF_MONTH_MARK_RE.search("напомни в 18")


def test_fast_regex_unchanged_for_simple_hour():
    """Обычный кейс без дня месяца по-прежнему бьётся быстрым regex'ом."""
    from nexus.handlers.tasks import _CLARIFY_REMINDER_RE

    m = _CLARIFY_REMINDER_RE.search("напомни в 18")
    assert m and m.group(1) == "18"


# ── handle_last_task_clarify: день месяца идёт через Haiku ─────────────────

@pytest.mark.asyncio
async def test_day_of_month_reminder_does_not_swap_day_and_hour():
    from nexus.handlers import tasks

    uid = 771_101
    page_id = "task-dom-1"
    msg, sent = _msg(uid, "напомни 20 числа в 18 часов")

    tasks._last_task_set(uid, page_id)
    update_calls = []

    async def capture(pid, props):
        update_calls.append((pid, props))

    correct_dt = "2026-08-20T18:00"

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture)), \
             patch.object(tasks._repo, "retrieve_page", AsyncMock(return_value=None)), \
             patch.object(tasks, "_scheduler", None), \
             patch.object(tasks, "save_task_reminder", AsyncMock()) as save_mock, \
             patch.object(tasks, "ask_claude",
                          AsyncMock(return_value='{"reminder_time": "%s"}' % correct_dt)) as ask_mock:
            handled = await tasks.handle_last_task_clarify(msg, msg.text, uid)

        assert handled is True
        # Быстрый regex не должен был сработать → парсинг ушёл в Haiku
        ask_mock.assert_awaited_once()
        assert update_calls, "set_props должен быть вызван"
        _, props = update_calls[-1]
        assert props["Напоминание"]["date"]["start"].startswith(correct_dt)

        # Плашка зарегистрирована — reply-исправление найдёт эту задачу
        save_mock.assert_awaited_once()
        call_args = save_mock.call_args.args
        assert call_args[0] == page_id
        assert call_args[2] == sent.message_id
    finally:
        tasks._last_task_del(uid)


@pytest.mark.asyncio
async def test_simple_hour_reminder_skips_haiku():
    """Без дня месяца — быстрый путь, Haiku не вызывается вообще."""
    from nexus.handlers import tasks

    uid = 771_102
    page_id = "task-dom-2"
    msg, _sent = _msg(uid, "напомни в 18")

    tasks._last_task_set(uid, page_id)
    update_calls = []

    async def capture(pid, props):
        update_calls.append((pid, props))

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture)), \
             patch.object(tasks._repo, "retrieve_page", AsyncMock(return_value=None)), \
             patch.object(tasks, "_scheduler", None), \
             patch.object(tasks, "save_task_reminder", AsyncMock()), \
             patch.object(tasks, "ask_claude", AsyncMock()) as ask_mock:
            handled = await tasks.handle_last_task_clarify(msg, msg.text, uid)

        assert handled is True
        ask_mock.assert_not_called()
        assert update_calls
    finally:
        tasks._last_task_del(uid)


@pytest.mark.asyncio
async def test_haiku_failure_leaves_task_untouched():
    """Если Haiku не смог распарсить — задача не трогается, флоу не падает."""
    from nexus.handlers import tasks

    uid = 771_103
    page_id = "task-dom-3"
    msg, _sent = _msg(uid, "напомни 20 числа в 18 часов")

    tasks._last_task_set(uid, page_id)

    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks._repo, "set_props", AsyncMock()) as set_props, \
             patch.object(tasks, "save_task_reminder", AsyncMock()) as save_mock, \
             patch.object(tasks, "ask_claude", AsyncMock(return_value="не json")):
            handled = await tasks.handle_last_task_clarify(msg, msg.text, uid)

        assert handled is False
        set_props.assert_not_called()
        save_mock.assert_not_called()
    finally:
        tasks._last_task_del(uid)


# ── shared parser reused by handle_reschedule_reminder ──────────────────────

@pytest.mark.asyncio
async def test_haiku_parse_reminder_dt_used_by_reschedule():
    """handle_reschedule_reminder теперь зовёт общий _haiku_parse_reminder_dt
    (не дублирует свой промпт) — так фикс для «N числа» покрывает и этот путь."""
    from nexus.handlers import tasks

    uid = 771_104
    msg, _sent = _msg(uid, "20 числа в 18 часов")
    # Дата должна быть в будущем — _is_future_dt отсекает прошлое (anti-loop),
    # иначе _schedule_reminder не вызывается. Не хардкодим август.
    from datetime import datetime, timedelta, timezone
    future_dt = (datetime.now(timezone(timedelta(hours=3))) + timedelta(days=15)).strftime("%Y-%m-%dT18:00")

    tasks._pending_set(uid, {"task_id": "t-dom", "action": "reschedule", "title": "почта"})
    try:
        with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
             patch.object(tasks, "ask_claude",
                          AsyncMock(return_value='{"reminder_time": "%s"}' % future_dt)), \
             patch.object(tasks, "_schedule_reminder", AsyncMock()) as sched, \
             patch.object(tasks, "_update_notion_on_reschedule", AsyncMock()) as notion, \
             patch.object(tasks, "react", AsyncMock()):
            await tasks.handle_reschedule_reminder(msg)

        sched.assert_awaited_once()
        notion.assert_awaited_once_with("t-dom", future_dt, 3)
    finally:
        tasks._pending_del(uid)
