"""tests/test_reminder_tz_local_wall.py — regression для трёх багов одной ветки:

1. Timezone (#143): reminder из PG приходит tz-aware ('...+00:00' = UTC). Срез
   `[:16]` выбрасывал offset, `_schedule_reminder` переклеивал как локальное →
   повторяющееся напоминание срабатывало на tz_offset часов раньше.
2. Digest: `_build_today_digest` показывал время напоминания в UTC и бакетил
   задачи по UTC-дате (тот же корень).
3. Task cancel: `handle_task_cancel` молча архивировал первую из равных по
   скору задач (отмена деструктивна) вместо уточнения.

Privacy: generic названия задач.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 1. _to_local_wall (pure) ─────────────────────────────────────────────────


def test_to_local_wall_utc_offset_converts_to_user_tz():
    from nexus.handlers.tasks import _to_local_wall
    # 10:40 UTC = 13:40 в UTC+3
    assert _to_local_wall("2026-06-18T10:40:00+00:00", 3) == "2026-06-18T13:40"


def test_to_local_wall_z_suffix():
    from nexus.handlers.tasks import _to_local_wall
    assert _to_local_wall("2026-06-18T10:40:00Z", 3) == "2026-06-18T13:40"


def test_to_local_wall_same_offset_is_noop():
    from nexus.handlers.tasks import _to_local_wall
    # +03:00 при tz_offset=3 — настенное время сохраняется (почему старые тесты
    # с суффиксом +03:00 не ловили баг)
    assert _to_local_wall("2026-05-08T16:00:00+03:00", 3) == "2026-05-08T16:00"


def test_to_local_wall_negative_offset():
    from nexus.handlers.tasks import _to_local_wall
    # 22:00 UTC = 17:00 в UTC-5
    assert _to_local_wall("2026-06-18T22:00:00+00:00", -5) == "2026-06-18T17:00"


def test_to_local_wall_naive_passes_through():
    from nexus.handlers.tasks import _to_local_wall
    assert _to_local_wall("2026-06-18T13:40", 3) == "2026-06-18T13:40"
    assert _to_local_wall("2026-06-18T13:40:00", 3) == "2026-06-18T13:40"


def test_to_local_wall_date_only_and_empty():
    from nexus.handlers.tasks import _to_local_wall
    assert _to_local_wall("2026-06-18", 3) == "2026-06-18"
    assert _to_local_wall("", 3) == ""


# ── 2. restore pass1: reminder +00:00 → локальное настенное время ────────────


@pytest.mark.asyncio
async def test_restore_pass1_utc_reminder_scheduled_at_local_wall():
    """#143: задача с будущим reminder '...+00:00' планируется на локальное
    настенное время (без сдвига на tz_offset)."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    # 10:40 UTC = 13:40 МСК; берём заведомо будущий год чтобы reminder был future
    fut = PgTask(
        id="fut-1",
        title="напоминание",
        repeat="Нет",
        reminder="2099-01-01T10:40:00+00:00",
        deadline="",
        user_notion_id="notion-user-x",
    )

    schedule_calls: list = []

    async def fake_schedule(chat_id, title, reminder_dt, task_id, tz_offset=3):
        schedule_calls.append((task_id, reminder_dt, tz_offset))

    fake_pg = MagicMock()
    fake_pg.active_with_future_reminder = AsyncMock(return_value=[fut])
    fake_pg.active_with_past_reminder = AsyncMock(return_value=[])
    fake_pg.active_recurring_without_reminder = AsyncMock(return_value=[])

    with patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot", MagicMock()), \
         patch.object(tasks, "_schedule_reminder", fake_schedule), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo", return_value=fake_pg), \
         patch("core.user_manager.get_user",
               AsyncMock(return_value={"notion_page_id": "notion-user-x"})), \
         patch("core.config.config") as mock_cfg:
        mock_cfg.allowed_ids = [999_001]
        await tasks.restore_reminders_on_startup()

    assert schedule_calls, "pass1 должен запланировать reminder"
    task_id, reminder_dt, tz_off = schedule_calls[0]
    assert task_id == "fut-1"
    assert reminder_dt == "2099-01-01T13:40", \
        f"ожидалось локальное 13:40 (МСК), got {reminder_dt}"


# ── 3. digest: время напоминания в локальном поясе ───────────────────────────


def _msk_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_digest_shows_local_reminder_time():
    """Digest: reminder '...+00:00' выводится в локальном времени и попадает
    в секцию «Сегодня» по локальной дате."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    today = _msk_today()
    # 10:40 UTC = 13:40 МСК, сегодня
    t = PgTask(
        id="d-1",
        title="позвонить в банк",
        status="Not started",
        priority="🔴 Срочно",
        category="🏠 Жильё",
        reminder=f"{today}T10:40:00+00:00",
        deadline="",
        repeat="Нет",
        user_notion_id="notion-user-x",
    )

    with patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks._repo, "active", AsyncMock(return_value=[t])), \
         patch.object(tasks, "ask_claude", AsyncMock(return_value="давай начнём")), \
         patch("nexus.handlers.streaks.get_streak", return_value={"streak": 0}), \
         patch("nexus.handlers.finance._calc_free_remaining",
               AsyncMock(return_value=None)):
        text = await tasks._build_today_digest(123, "notion-user-x")

    assert "позвонить в банк" in text
    assert "13:40" in text, f"ожидалось локальное 13:40 в дайджесте:\n{text}"
    assert "10:40" not in text, f"UTC-время не должно протечь:\n{text}"


# ── 4. task cancel: неоднозначность → уточнение, не слепая отмена ─────────────


@pytest.mark.asyncio
async def test_cancel_ambiguous_asks_to_clarify_without_archiving():
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    def _mk(tid, title):
        return PgTask(id=tid, title=title, repeat="Нет",
                      user_notion_id="notion-user-x")

    # Обе содержат «позвонить» → одинаковый скор = тай
    two = [_mk("c-1", "позвонить маме"), _mk("c-2", "позвонить врачу")]

    msg = MagicMock()
    msg.answer = AsyncMock()

    set_archived = AsyncMock()
    with patch.object(tasks._repo, "active", AsyncMock(return_value=two)), \
         patch.object(tasks._repo, "set_archived", set_archived):
        await tasks.handle_task_cancel(msg, "отмени задачу позвонить",
                                       user_notion_id="notion-user-x")

    set_archived.assert_not_called()
    sent = msg.answer.call_args[0][0]
    assert "несколько" in sent.lower()
    assert "позвонить маме" in sent and "позвонить врачу" in sent


@pytest.mark.asyncio
async def test_cancel_unique_match_archives():
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    def _mk(tid, title):
        return PgTask(id=tid, title=title, repeat="Нет",
                      user_notion_id="notion-user-x")

    tasks_list = [_mk("u-1", "оплатить интернет"), _mk("u-2", "позвонить маме")]

    msg = MagicMock()
    msg.answer = AsyncMock()

    set_archived = AsyncMock()
    with patch.object(tasks._repo, "active", AsyncMock(return_value=tasks_list)), \
         patch.object(tasks._repo, "set_archived", set_archived), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "delete_task_reminder", AsyncMock()):
        await tasks.handle_task_cancel(msg, "отмени задачу интернет",
                                       user_notion_id="notion-user-x")

    set_archived.assert_called_once_with("u-1")
    sent = msg.answer.call_args[0][0]
    assert "отменена" in sent
