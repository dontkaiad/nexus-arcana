"""Regression: задача «сегодня с 14 до 17 забрать аттестат с вуза» (deadline=14:00
как НАЧАЛО дела, duration_min=180 — та же конвенция, что в core/booking/busy.py
#241: end = deadline + duration_min) получала дедлайн-пинг «Сделал?» ровно
в 14:00 — то есть в момент, когда дело только НАЧИНАЕТСЯ, а не должно быть
закончено. Плюс на этом пинге было только 2 кнопки (Выполнено!/Отложить) —
не было «В процессе», хотя дело реально ещё идёт (длится 3 часа).

Фикс: `_schedule_deadline_check` принимает duration_min и сдвигает время
проверки на конец дела (deadline + duration), плюс третья кнопка
«🔧 В процессе» (переиспользует уже существующий task_wip_ callback).

Privacy: синтетические id/title.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import tasks as T


@pytest.mark.asyncio
async def test_deadline_check_with_duration_fires_at_end_not_start():
    sched = MagicMock()
    run_dates = {}

    def _add_job(fn, trigger, run_date, id, replace_existing):
        run_dates["run_date"] = run_date

    sched.add_job.side_effect = _add_job

    deadline_local = (datetime.now(timezone(timedelta(hours=3))) + timedelta(hours=2))
    deadline_str = deadline_local.strftime("%Y-%m-%dT%H:%M")

    with patch.object(T, "_scheduler", sched), \
         patch.object(T, "_bot", MagicMock()):
        await T._schedule_deadline_check(
            777, "забрать аттестат с вуза", deadline_str, "t-1", tz_offset=3,
            duration_min=180,
        )

    expected = datetime.strptime(deadline_str, "%Y-%m-%dT%H:%M").replace(
        tzinfo=timezone(timedelta(hours=3))
    ) + timedelta(minutes=180)
    assert run_dates["run_date"] == expected


@pytest.mark.asyncio
async def test_deadline_check_without_duration_fires_at_deadline_as_before():
    sched = MagicMock()
    run_dates = {}

    def _add_job(fn, trigger, run_date, id, replace_existing):
        run_dates["run_date"] = run_date

    sched.add_job.side_effect = _add_job

    deadline_local = (datetime.now(timezone(timedelta(hours=3))) + timedelta(hours=2))
    deadline_str = deadline_local.strftime("%Y-%m-%dT%H:%M")

    with patch.object(T, "_scheduler", sched), \
         patch.object(T, "_bot", MagicMock()):
        await T._schedule_deadline_check(
            777, "сдать отчёт", deadline_str, "t-2", tz_offset=3,
        )

    expected = datetime.strptime(deadline_str, "%Y-%m-%dT%H:%M").replace(
        tzinfo=timezone(timedelta(hours=3))
    )
    assert run_dates["run_date"] == expected


@pytest.mark.asyncio
async def test_deadline_check_message_has_three_buttons_including_wip():
    sched = MagicMock()
    fired = {}

    def _add_job(fn, trigger, run_date, id, replace_existing):
        fired["fn"] = fn

    sched.add_job.side_effect = _add_job
    bot = MagicMock()
    sent = MagicMock(chat=MagicMock(id=777), message_id=1)

    async def _send_message(*args, **kwargs):
        fired["kb"] = kwargs.get("reply_markup")
        return sent

    bot.send_message = _send_message

    deadline_local = (datetime.now(timezone(timedelta(hours=3))) + timedelta(hours=2))
    deadline_str = deadline_local.strftime("%Y-%m-%dT%H:%M")

    with patch.object(T, "_scheduler", sched), \
         patch.object(T, "_bot", bot), \
         patch.object(T, "_repo") as m_repo, \
         patch.object(T, "save_task_reminder"):
        m_repo.retrieve_page = _async_none
        await T._schedule_deadline_check(
            777, "забрать аттестат с вуза", deadline_str, "t-3", tz_offset=3,
        )
        await fired["fn"]()

    kb = fired["kb"]
    all_buttons = [b for row in kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for b in all_buttons]
    assert len(all_buttons) == 3
    assert "task_complete_t-3" in callbacks
    assert "task_reschedule_t-3" in callbacks
    assert "task_wip_t-3" in callbacks


async def _async_none(*args, **kwargs):
    return None


def _make_deadline_call(title: str, task_id: str, callback_data: str):
    call = MagicMock()
    call.from_user = MagicMock(id=777)
    call.data = callback_data
    call.message = MagicMock()
    call.message.text = f"⏰ Дедлайн: {title}\n\nСделал?"
    call.message.edit_reply_markup = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()
    return call


@pytest.mark.asyncio
async def test_task_wip_from_deadline_message_extracts_clean_title():
    """Раньше task_wip не знал про формат «Дедлайн: title», title оставался
    пустым; task_reschedule знал, но резал по «.», а не по переносу строки —
    «title\\n\\nСделал?» целиком утекало в заголовок следующего напоминания."""
    call = _make_deadline_call("забрать аттестат с вуза", "t-4", "task_wip_t-4")

    with patch.object(T, "_repo") as m_repo, \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "delete_task_reminder", _async_none):
        m_repo.set_in_progress = _async_none
        await T.task_wip(call)

    saved = pset.call_args.args[1]
    assert saved["title"] == "забрать аттестат с вуза"


@pytest.mark.asyncio
async def test_task_reschedule_from_deadline_message_extracts_clean_title():
    call = _make_deadline_call("забрать аттестат с вуза", "t-5", "task_reschedule_t-5")

    with patch.object(T, "_pending_set") as pset:
        await T.task_reschedule(call)

    saved = pset.call_args.args[1]
    assert saved["title"] == "забрать аттестат с вуза"
