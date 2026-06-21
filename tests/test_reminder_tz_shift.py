"""tests/test_reminder_tz_shift.py — регресс issue #143.

Повторяющееся напоминание «оживало» из PG (`TIMESTAMP(timezone=True)` → ISO с
явным `+00:00`) и планировалось на tz_offset часов раньше: срез `[:16]`
выбрасывал offset, а `_schedule_reminder` переклеивал время как локальное
`+tz_offset`. Симптомы Кай: напоминание приходило на 3 ч раньше (10:40 вместо
13:40, МСК UTC+3) или, если 3-ч-раньше уже прошло на момент рестарта, не
приходило вовсе (`_schedule_reminder` отбрасывает прошлое >120с).

Фикс — `_to_local_wall(iso, tz_offset)`: honor явный offset (astimezone → пояс
пользователя) → наивное локальное настенное время. Прогнан через проход 1/2
restore, `_handle_recurring_task_reset` и `_next_cycle_date`.

Privacy: generic названия задач.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _to_local_wall: корректно для +00:00 / +03:00 / наивных / date-only ───────

def test_to_local_wall_utc_to_msk():
    from nexus.handlers.tasks import _to_local_wall
    # 10:40 UTC == 13:40 МСК → должно вернуть локальные 13:40, без сдвига
    assert _to_local_wall("2026-06-18T10:40:00+00:00", 3) == "2026-06-18T13:40"


def test_to_local_wall_already_local_offset_noop():
    from nexus.handlers.tasks import _to_local_wall
    # уже +03:00 → настенное время сохраняется
    assert _to_local_wall("2026-06-18T13:40:00+03:00", 3) == "2026-06-18T13:40"


def test_to_local_wall_naive_is_local():
    from nexus.handlers.tasks import _to_local_wall
    # наивная строка уже локальная — не трогаем (только нормализуем до HH:MM)
    assert _to_local_wall("2026-06-18T13:40", 3) == "2026-06-18T13:40"
    assert _to_local_wall("2026-06-18T13:40:00", 3) == "2026-06-18T13:40"


def test_to_local_wall_z_suffix():
    from nexus.handlers.tasks import _to_local_wall
    assert _to_local_wall("2026-06-18T10:40:00Z", 3) == "2026-06-18T13:40"


def test_to_local_wall_date_only_untouched():
    from nexus.handlers.tasks import _to_local_wall
    assert _to_local_wall("2026-06-18", 3) == "2026-06-18"


def test_to_local_wall_other_tz():
    from nexus.handlers.tasks import _to_local_wall
    # Екатеринбург UTC+5: 10:40 UTC == 15:40
    assert _to_local_wall("2026-06-18T10:40:00+00:00", 5) == "2026-06-18T15:40"


def test_to_local_wall_empty():
    from nexus.handlers.tasks import _to_local_wall
    assert _to_local_wall("", 3) == ""


# ── restore проход 1: reminder из PG (+00:00) → правильное локальное время ────

@pytest.mark.asyncio
async def test_restore_pass1_utc_reminder_no_shift():
    """Проход 1: будущий reminder `...+00:00` планируется на локальное настенное
    время (13:40), а НЕ на UTC-часы, переклеенные как локальные (10:40)."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.pg_tasks_repo as pgt

    # Далёкое будущее, чтобы попасть в проход 1 при любой дате прогона теста.
    utc_reminder = "2099-12-31T10:40:00+00:00"   # == 13:40 МСК
    task = Task(id="t-1", title="generic task", repeat="Ежедневно",
                repeat_time="13:40", reminder=utc_reminder)

    fake_user = {"permissions": {"nexus": True}, "notion_page_id": "u-1"}

    with patch("core.config.config.allowed_ids", [7]), \
         patch("core.user_manager.get_user", AsyncMock(return_value=fake_user)), \
         patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot", MagicMock()), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks, "_schedule_reminder", AsyncMock()) as m_sched, \
         patch.object(pgt.PgTasksRepo, "active_with_future_reminder",
                      AsyncMock(return_value=[task])), \
         patch.object(pgt.PgTasksRepo, "active_with_past_reminder",
                      AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_recurring_without_reminder",
                      AsyncMock(return_value=[])):
        await tasks.restore_reminders_on_startup()

    m_sched.assert_awaited_once()
    args = m_sched.await_args.args
    # сигнатура: (chat_id, title, reminder_dt, task_id, tz_offset)
    reminder_dt = args[2]
    assert reminder_dt == "2099-12-31T13:40", \
        f"ожидалось локальное 13:40, получили {reminder_dt} (сдвиг tz_offset = баг #143)"


# ── _next_cycle_date: offset-несущая строка не ломает настенное время ─────────

def test_next_cycle_date_utc_input_preserves_wall_time():
    """Вход с `+00:00` не должен протечь UTC-часами в результат (override_time)."""
    from nexus.handlers.tasks import _next_cycle_date
    # 10:40 UTC == 13:40 МСК; override_time канонический = 13:40
    out = _next_cycle_date("2026-06-18T10:40:00+00:00", "Ежедневно",
                           tz_offset=3, override_time="13:40")
    assert out.endswith("T13:40"), f"ожидался T13:40, got {out}"
    assert "T10:40" not in out


def test_next_cycle_date_utc_input_no_override_uses_local_time():
    """Без override_time время берётся из строки — после нормализации локальное."""
    from nexus.handlers.tasks import _next_cycle_date
    out = _next_cycle_date("2026-06-18T10:40:00+00:00", "Ежедневно", tz_offset=3)
    assert out.endswith("T13:40"), f"ожидался T13:40 (локальное), got {out}"
