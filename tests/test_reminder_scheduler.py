"""tests/test_reminder_scheduler.py — общий планировщик напоминаний."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.reminder_scheduler import ReminderScheduler, _ensure_datetime


def test_ensure_datetime_passes_iso_through():
    assert _ensure_datetime("2026-05-05T18:00:00") == "2026-05-05T18:00"
    assert _ensure_datetime("2026-05-05T09:00") == "2026-05-05T09:00"


def test_ensure_datetime_fills_default_time_for_date_only():
    assert _ensure_datetime("2026-05-05") == "2026-05-05T09:00"


def test_ensure_datetime_empty():
    assert _ensure_datetime("") == ""
    assert _ensure_datetime(None) == ""


def test_default_callback_prefix_is_work():
    f = ReminderScheduler()
    assert f.callback_prefix == "work"


def test_callback_prefix_can_override():
    f = ReminderScheduler(callback_prefix="task")
    kb = f._build_reminder_kb("page-1")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert all(c.startswith("task_") for c in cbs)


def test_ready_false_before_init():
    f = ReminderScheduler()
    assert f.ready is False


def test_ready_true_after_init():
    f = ReminderScheduler()
    f.init(MagicMock(), MagicMock())
    assert f.ready is True


@pytest.mark.asyncio
async def test_schedule_reminder_skipped_when_not_ready():
    f = ReminderScheduler()
    ok = await f.schedule_reminder(
        chat_id=1, title="x", reminder_dt="2026-05-05T18:00",
        page_id="p", tz_offset=3,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_schedule_reminder_adds_apscheduler_job_for_future():
    f = ReminderScheduler()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    sched = MagicMock()
    sched.add_job = MagicMock()
    f.init(bot, sched)

    # Ставим напоминание в далёком будущем
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    ok = await f.schedule_reminder(
        chat_id=42, title="Сделать ритуал", reminder_dt=future,
        page_id="abc-123", tz_offset=3,
    )
    assert ok is True
    sched.add_job.assert_called_once()
    job_kwargs = sched.add_job.call_args.kwargs
    assert job_kwargs["id"] == "reminder_abc-123"
    assert job_kwargs["replace_existing"] is True


@pytest.mark.asyncio
async def test_schedule_reminder_sends_immediately_if_just_passed():
    """Дата на 30 секунд в прошлом — отправляем сразу, не планируем."""
    f = ReminderScheduler()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    sched = MagicMock()
    sched.add_job = MagicMock()
    f.init(bot, sched)

    past = (datetime.now(timezone(timedelta(hours=3))) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M")
    ok = await f.schedule_reminder(
        chat_id=42, title="late", reminder_dt=past, page_id="p", tz_offset=3,
    )
    assert ok is True
    bot.send_message.assert_awaited_once()
    sched.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_reminder_skips_if_far_in_past():
    f = ReminderScheduler()
    f.init(MagicMock(), MagicMock())
    long_past = (datetime.now(timezone(timedelta(hours=3))) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    ok = await f.schedule_reminder(
        chat_id=42, title="x", reminder_dt=long_past, page_id="p", tz_offset=3,
    )
    assert ok is False


def test_remove_jobs_calls_remove_for_both_prefixes():
    f = ReminderScheduler()
    sched = MagicMock()
    sched.remove_job = MagicMock()
    f.init(MagicMock(), sched)
    f.remove_jobs("xyz")
    calls = [c.args[0] for c in sched.remove_job.call_args_list]
    assert "reminder_xyz" in calls
    assert "deadline_xyz" in calls
