"""tests/test_recurring_task_status.py — повторяющиеся задачи: статус
«In progress» после клика «Сделано» + Mini App скрывает выполненные сегодня.

Контекст (BACKLOG.md Bug #1):
- Раньше _handle_recurring_task_reset перезаписывал статус в Not started,
  что противоречило ожиданию Кай. Теперь — In progress + Время завершения=now.
- Mini App today.py теперь читает «Время завершения» и не показывает в
  расписании сегодня повторяющиеся задачи, выполненные сегодня.

Privacy: тесты используют generic названия задач и обобщённые группы.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fix A: _handle_recurring_task_reset ──────────────────────────────────────


def _make_task(*, repeat="Ежедневно", repeat_time="16:00|every_2d",
               deadline="", reminder=""):
    """Task domain object для повторяющейся задачи."""
    from nexus.repos.pg_tasks_repo import Task
    return Task(
        id="test-task-id",
        title="тестовая задача",
        repeat=repeat,
        repeat_time=repeat_time or "",
        deadline=deadline or "",
        reminder=reminder or "",
    )


def _make_message():
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = 999_001
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_reset_recurring_no_deadline_sets_in_progress_and_completion():
    """Recurring без дедлайна → Status=In progress + Время завершения=now."""
    from nexus.handlers import tasks

    msg = _make_message()
    task = _make_task(deadline="", reminder="2026-05-08T16:00:00+03:00")

    update_calls: list = []

    async def capture_update(task_id, props_dict):
        update_calls.append((task_id, props_dict))

    with patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-1", task, "Ежедневно", "тестовая задача", uid=999_001,
        )

    assert update_calls, "set_props должен быть вызван"
    _, update_props = update_calls[-1]

    # Status → In progress
    assert update_props["Статус"]["status"]["name"] == "In progress"
    # Время завершения должно быть в props (date dict)
    assert "Время завершения" in update_props
    completion_iso = update_props["Время завершения"]["date"]["start"]
    # Проверяем что это сегодня (в Europe/Moscow)
    today_moscow = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    assert completion_iso.startswith(today_moscow)


@pytest.mark.asyncio
async def test_reset_recurring_with_deadline_keeps_in_progress():
    """Recurring с дедлайном → тоже In progress + completion."""
    from nexus.handlers import tasks

    msg = _make_message()
    task = _make_task(
        deadline="2026-06-01",
        reminder="2026-06-01T09:00:00+03:00",
    )

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-2", task, "Ежедневно", "test task", uid=999_001,
        )

    assert captured["props"]["Статус"]["status"]["name"] == "In progress"
    assert "Время завершения" in captured["props"]
    # Дедлайн сдвинут (был 2026-06-01, должен стать позже)
    assert "Дедлайн" in captured["props"]


@pytest.mark.asyncio
async def test_reset_recurring_uses_canonical_time_after_snooze():
    """issue #67: после снуза напоминания reset должен вернуть HH:MM из
    «Время повтора» (16:00), а не использовать снузенное время (20:29)."""
    from nexus.handlers import tasks

    msg = _make_message()
    # repeat_time = «16:00|every_2d», но текущее напоминание снузено на 20:29
    task = _make_task(
        repeat_time="16:00|every_2d",
        deadline="",
        reminder="2026-05-10T20:29:00+03:00",
    )

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-snooze", task, "Ежедневно", "менять лоток", uid=999_001,
        )

    new_reminder = captured["props"]["Напоминание"]["date"]["start"]
    # Каноническое время 16:00 восстановлено, снузенное 20:29 не утечёт в цикл
    assert "T16:00" in new_reminder, f"Ожидался HH:MM=16:00, got {new_reminder}"
    assert "T20:29" not in new_reminder


@pytest.mark.asyncio
async def test_recurring_reminder_done_already_in_progress():
    """_handle_recurring_reminder_done должен ставить In progress (legacy путь)."""
    from nexus.handlers import tasks

    msg = _make_message()

    with patch.object(tasks._repo, "set_in_progress", AsyncMock()) as mock_ip:
        await tasks._handle_recurring_reminder_done(msg, "task-id-3", "test task")

    mock_ip.assert_called_once_with("task-id-3")


@pytest.mark.asyncio
async def test_completion_timestamp_format_moscow_tz():
    """Время завершения должно быть в Moscow tz (+03:00) и валидным ISO."""
    from nexus.handlers import tasks

    msg = _make_message()
    task = _make_task(deadline="", reminder="")

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-4", task, "Ежедневно", "x", uid=999_001,
        )

    iso = captured["props"]["Время завершения"]["date"]["start"]
    # Парсится как ISO
    parsed = datetime.fromisoformat(iso)
    assert parsed.utcoffset() == timedelta(hours=3), \
        f"expected Moscow tz +03:00, got {parsed.utcoffset()}"


# ── Fix B: today.py фильтр completed_today ──────────────────────────────────

FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-fix-1"


@pytest.fixture(autouse=True)
def isolated_cache_b(tmp_path, monkeypatch):
    from miniapp.backend import cache
    db_file = tmp_path / "adhd_cache.db"
    monkeypatch.setattr(cache, "_DB_PATH", str(db_file))
    cache._init_db()
    yield


@pytest.fixture
def client():
    from miniapp.backend.app import app
    from miniapp.backend.auth import current_user_id

    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _today_local_date(tz_offset: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).date()


def _make_task_page(task_id, title, *, status="Not started", prio="🔴 Срочно",
                    cat="🏠 Жильё", deadline="", reminder="",
                    repeat_time="", repeat=None, completed=""):
    from nexus.repos.pg_tasks_repo import Task as PgTask
    return PgTask(
        id=task_id,
        title=title,
        status=status,
        priority=prio,
        category=cat,
        deadline=deadline or "",
        reminder=reminder or "",
        repeat_time=repeat_time or "",
        repeat=repeat or "Нет",
        completed_at=completed or "",
        user_notion_id=FAKE_NOTION_USER,
    )


def _today_get_response(client, tasks):
    today_date = _today_local_date(3)
    with patch("miniapp.backend.routes.today._tasks_repo.active", AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.today._budget_repo.query", AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.today.budget_day_limit_from_plan", AsyncMock(return_value=0)), \
         patch("miniapp.backend.routes.today.ask_claude",
               AsyncMock(return_value="tip")), \
         patch("miniapp.backend.routes.today.today_user_tz",
               AsyncMock(return_value=(today_date, 3))), \
         patch("miniapp.backend.routes.today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("nexus.handlers.streaks.get_streak", return_value={
             "streak": 0, "best": 0, "last_activity_date": str(today_date),
             "rest_day_date": None, "rest_days_used": 0,
             "streak_start_date": str(today_date),
         }), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=True):
        return client.get("/api/today")


def test_recurring_completed_today_hidden_from_schedule(client):
    """Recurring + completed=сегодня утром → не в scheduled."""
    today_local = _today_local_date(3)
    today_str = today_local.isoformat()
    completed_morning = f"{today_str}T08:00:00+03:00"
    reminder_today = f"{today_str}T16:00:00+03:00"

    tasks = [
        _make_task_page(
            "t-done-today", "повторяющаяся выполнена",
            status="In progress", repeat="Ежедневно",
            repeat_time="16:00|every_2d",
            reminder=reminder_today,
            completed=completed_morning,
        ),
    ]

    resp = _today_get_response(client, tasks)
    assert resp.status_code == 200
    data = resp.json()
    sched_ids = [s["id"] for s in data.get("scheduled", [])]
    assert "t-done-today" not in sched_ids, \
        "выполненная сегодня recurring задача должна быть скрыта из scheduled"


def test_recurring_completed_yesterday_shown_when_today_is_occurrence(client):
    """Recurring + completed=вчера, сегодня=вхождение → в scheduled."""
    today_local = _today_local_date(3)
    today_str = today_local.isoformat()
    yesterday = (today_local - timedelta(days=1)).isoformat()
    completed_yesterday = f"{yesterday}T08:00:00+03:00"
    # Anchor = сегодня → today_date - anchor = 0 days, % 1 == 0 → occurrence
    reminder_today = f"{today_str}T16:00:00+03:00"

    tasks = [
        _make_task_page(
            "t-done-yest", "повторяющаяся вчерашняя",
            status="In progress", repeat="Ежедневно",
            repeat_time="16:00|every_1d",
            reminder=reminder_today,
            completed=completed_yesterday,
        ),
    ]

    resp = _today_get_response(client, tasks)
    assert resp.status_code == 200
    data = resp.json()
    sched_ids = [s["id"] for s in data.get("scheduled", [])]
    assert "t-done-yest" in sched_ids, \
        "вчерашнее выполнение не должно скрывать сегодняшнее вхождение"


def test_recurring_no_completion_default_behavior(client):
    """Recurring без completed_at → стандартное поведение (показ если вхождение сегодня)."""
    today_local = _today_local_date(3)
    today_str = today_local.isoformat()
    reminder_today = f"{today_str}T16:00:00+03:00"

    tasks = [
        _make_task_page(
            "t-fresh", "свежая повторяющаяся",
            status="Not started", repeat="Ежедневно",
            repeat_time="16:00|every_1d",
            reminder=reminder_today,
            completed=None,
        ),
    ]

    resp = _today_get_response(client, tasks)
    assert resp.status_code == 200
    data = resp.json()
    sched_ids = [s["id"] for s in data.get("scheduled", [])]
    assert "t-fresh" in sched_ids


def test_non_recurring_completion_field_does_not_break_today(client):
    """Non-recurring задача с completed_at не должна сломать today.py.

    Done/Complete всё равно фильтруются на уровне _fetch_nexus_tasks; а если
    Не закрыта — completion_today не должен прятать (т.к. repeat_time пуст).
    """
    today_local = _today_local_date(3)
    today_str = today_local.isoformat()
    completed_morning = f"{today_str}T08:00:00+03:00"

    tasks = [
        _make_task_page(
            "t-nonrec", "обычная задача с completed",
            status="Not started", repeat=None, repeat_time="",
            deadline=today_str, completed=completed_morning,
        ),
    ]

    resp = _today_get_response(client, tasks)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Без repeat_time guard не срабатывает — задача попадает в today_no_time
    # или scheduled (зависит от наличия времени дедлайна).
    all_ids = (
        [s["id"] for s in data.get("scheduled", [])] +
        [t["id"] for t in data.get("tasks", [])]
    )
    assert "t-nonrec" in all_ids, \
        "non-recurring задача с completed_at должна остаться видимой"


# ── Fix C: #137 — recurring с repeat_time, reminder IS NULL ─────────────────


@pytest.mark.asyncio
async def test_reset_recurring_null_reminder_computes_first_run():
    """_handle_recurring_task_reset: reminder пуст + repeat_time задан →
    new_reminder вычисляется из repeat_time, PG и scheduler обновляются.

    Это фикс #137: раньше new_reminder оставался '', PG не писался, scheduler
    не ставился, задача молчала вечно.
    """
    from nexus.handlers import tasks

    msg = _make_message()
    # reminder = "" (NULL-задача), repeat_time = "16:00|every_2d"
    task = _make_task(repeat_time="16:00|every_2d", deadline="", reminder="")

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-null-rem", task, "Ежедневно", "напоминай каждые 2 дня", uid=999_001,
        )

    assert captured, "set_props должен быть вызван"
    assert "Напоминание" in captured["props"], "Напоминание должно быть записано в PG"
    reminder_iso = captured["props"]["Напоминание"]["date"]["start"]
    # Должно содержать 16:00 (canonical time из repeat_time)
    assert "T16:00" in reminder_iso, f"Ожидался T16:00 в reminder, got {reminder_iso}"
    # Должно быть в будущем: дата >= сегодня
    from datetime import timezone as _tz
    now_local = datetime.now(_tz(timedelta(hours=3)))
    reminder_dt = datetime.fromisoformat(reminder_iso.replace("+03:00", ""))
    assert reminder_dt > now_local.replace(tzinfo=None), \
        f"reminder должен быть в будущем, got {reminder_iso}"


@pytest.mark.asyncio
async def test_restore_pass3_revives_recurring_without_reminder():
    """restore_reminders_on_startup проход 3: задача с repeat_time и reminder=NULL
    получает reminder в PG и APScheduler job (#137).
    """
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    orphan = PgTask(
        id="orphan-1",
        title="каждые 2 дня",
        repeat="Ежедневно",
        repeat_time="16:00|every_2d",
        reminder="",
        deadline="",
        user_notion_id="notion-user-x",
    )

    set_props_calls: list = []
    schedule_calls: list = []

    async def fake_set_props(task_id, props):
        set_props_calls.append((task_id, props))

    async def fake_schedule(chat_id, title, reminder_dt, task_id, tz_offset=3):
        schedule_calls.append((task_id, reminder_dt))

    fake_pg = MagicMock()
    fake_pg.active_with_future_reminder = AsyncMock(return_value=[])
    fake_pg.active_with_past_reminder = AsyncMock(return_value=[])
    fake_pg.active_recurring_without_reminder = AsyncMock(return_value=[orphan])

    fake_bot = MagicMock()
    fake_scheduler = MagicMock()

    with patch.object(tasks, "_scheduler", fake_scheduler), \
         patch.object(tasks, "_bot", fake_bot), \
         patch.object(tasks._repo, "set_props", AsyncMock(side_effect=fake_set_props)), \
         patch.object(tasks, "_schedule_reminder", fake_schedule), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo", return_value=fake_pg), \
         patch("core.user_manager.get_user",
               AsyncMock(return_value={"notion_page_id": "notion-user-x"})), \
         patch("core.config.config") as mock_cfg:
        mock_cfg.allowed_ids = [999_001]
        await tasks.restore_reminders_on_startup()

    assert set_props_calls, "set_props должен быть вызван для orphan"
    task_id, props = set_props_calls[0]
    assert task_id == "orphan-1"
    assert "Напоминание" in props

    reminder_iso = props["Напоминание"]["date"]["start"]
    assert "T16:00" in reminder_iso, f"Ожидался T16:00, got {reminder_iso}"

    assert schedule_calls, "scheduler должен получить job для orphan"
    assert schedule_calls[0][0] == "orphan-1"


# ── #143: restore pass 1 — reminder из PG (UTC) не сдвигается на tz_offset ───


@pytest.mark.asyncio
async def test_restore_pass1_utc_reminder_keeps_local_wall_time():
    """restore_reminders_on_startup проход 1: reminder в PG хранится с явным
    offset UTC ('...+00:00'). _schedule_reminder должен получить ЛОКАЛЬНОЕ
    настенное время, а не UTC-часы (#143 — повтор срабатывал на 3ч раньше).

    11:00 UTC при tz_offset=3 = 14:00 МСК. Раньше [:16] выкидывал +00:00 и
    14:00 МСК превращалось в 11:00 МСК (сдвиг на 3ч раньше).
    """
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    task_utc = PgTask(
        id="utc-rem-1",
        title="менять лоток котам",
        repeat="Ежедневно",
        repeat_time="14:00|every_1d",
        # 11:00 UTC = 14:00 МСК (offset 3); дальняя дата → future-reminder
        reminder="2099-01-15T11:00:00+00:00",
        deadline="",
        user_notion_id="notion-user-z",
    )

    schedule_calls: list = []

    async def fake_schedule(chat_id, title, reminder_dt, task_id, tz_offset=3):
        schedule_calls.append((task_id, reminder_dt, tz_offset))

    fake_pg = MagicMock()
    fake_pg.active_with_future_reminder = AsyncMock(return_value=[task_utc])
    fake_pg.active_with_past_reminder = AsyncMock(return_value=[])
    fake_pg.active_recurring_without_reminder = AsyncMock(return_value=[])

    fake_bot = MagicMock()
    fake_scheduler = MagicMock()

    with patch.object(tasks, "_scheduler", fake_scheduler), \
         patch.object(tasks, "_bot", fake_bot), \
         patch.object(tasks, "_schedule_reminder", fake_schedule), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo", return_value=fake_pg), \
         patch("core.user_manager.get_user",
               AsyncMock(return_value={"notion_page_id": "notion-user-z"})), \
         patch("core.config.config") as mock_cfg:
        mock_cfg.allowed_ids = [999_001]
        await tasks.restore_reminders_on_startup()

    assert schedule_calls, "scheduler должен получить job для utc-rem-1"
    task_id, reminder_str, tz_off = schedule_calls[0]
    assert task_id == "utc-rem-1"
    assert tz_off == 3
    # Локальное настенное время 14:00 (НЕ 11:00 — иначе сдвиг на 3ч раньше)
    assert "T14:00" in reminder_str, \
        f"Ожидался T14:00 (локальное МСК), got {reminder_str}"
    assert "T11:00" not in reminder_str, \
        f"reminder не должен нести UTC-часы 11:00, got {reminder_str}"


def test_to_local_wall_honors_explicit_offset():
    """_to_local_wall: UTC-строка → локальное настенное время; наивная — как есть."""
    from nexus.handlers import tasks

    # 11:00 UTC = 14:00 при offset 3
    assert tasks._to_local_wall("2026-06-18T11:00:00+00:00", 3) == "2026-06-18T14:00"
    # +03:00 при offset 3 — настенное время неизменно
    assert tasks._to_local_wall("2026-06-18T14:00:00+03:00", 3) == "2026-06-18T14:00"
    # наивная локальная строка — режется до минут как есть
    assert tasks._to_local_wall("2026-06-18T14:00", 3) == "2026-06-18T14:00"
    # offset 5: 11:00 UTC = 16:00
    assert tasks._to_local_wall("2026-06-18T11:00:00+00:00", 5) == "2026-06-18T16:00"


# ── Fix D: смена часового пояса → пересборка APScheduler jobs ────────────────


@pytest.mark.asyncio
async def test_reschedule_all_for_tz_preserves_local_clock_time():
    """_reschedule_all_for_tz: смена UTC+5 → UTC+3 сохраняет H:M локального
    времени (16:00) и пересчитывает UTC.

    Reminder хранится как 11:00 UTC (= 16:00 UTC+5).
    После смены на UTC+3 job должен встать на 16:00+03:00 (= 13:00 UTC).
    """
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task as PgTask

    # Reminder 11:00 UTC = 16:00 UTC+5
    task_with_reminder = PgTask(
        id="tz-task-1",
        title="утренний ритуал",
        repeat="Ежедневно",
        repeat_time="16:00|every_1d",
        reminder="2026-06-19T11:00:00+00:00",
        deadline="",
        user_notion_id="notion-user-y",
    )

    set_props_calls: list = []
    schedule_calls: list = []

    async def fake_set_props(task_id, props):
        set_props_calls.append((task_id, props))

    async def fake_schedule(chat_id, title, reminder_dt, task_id, tz_offset=3):
        schedule_calls.append((task_id, reminder_dt, tz_offset))

    fake_pg = MagicMock()
    fake_pg.active_with_future_reminder = AsyncMock(return_value=[task_with_reminder])

    fake_scheduler = MagicMock()

    with patch.object(tasks._repo, "set_props", AsyncMock(side_effect=fake_set_props)), \
         patch.object(tasks, "_schedule_reminder", fake_schedule), \
         patch.object(tasks, "_scheduler", fake_scheduler), \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo", return_value=fake_pg), \
         patch("core.user_manager.get_user",
               AsyncMock(return_value={"notion_page_id": "notion-user-y"})):
        await tasks._reschedule_all_for_tz(
            uid=999_001,
            chat_id=999_001,
            old_offset=5,
            new_offset=3,
        )

    # PG обновлён с новым суффиксом +03:00
    assert set_props_calls, "set_props должен быть вызван"
    _, props = set_props_calls[0]
    assert "Напоминание" in props
    new_reminder_iso = props["Напоминание"]["date"]["start"]
    # Локальное время 16:00 сохранено, суффикс изменился
    assert "T16:00" in new_reminder_iso, f"Ожидался T16:00, got {new_reminder_iso}"
    assert "+03:00" in new_reminder_iso, f"Ожидался суффикс +03:00, got {new_reminder_iso}"

    # APScheduler job поставлен с новым tz_offset=3
    assert schedule_calls, "scheduler должен получить новый job"
    task_id, reminder_str, tz_off = schedule_calls[0]
    assert task_id == "tz-task-1"
    assert "T16:00" in reminder_str, f"Ожидался T16:00 в reminder_str, got {reminder_str}"
    assert tz_off == 3, f"Ожидался tz_offset=3, got {tz_off}"

    # Старый job снят
    fake_scheduler.remove_job.assert_called_with("reminder_tz-task-1")
