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


def _make_task_props(*, repeat="Ежедневно", repeat_time="16:00|every_2d",
                     deadline=None, reminder=None):
    """Минимальный props dict для повторяющейся задачи."""
    props: dict = {
        "Повтор": {"select": {"name": repeat}},
        "Время повтора": {
            "rich_text": [{"plain_text": repeat_time}] if repeat_time else []
        },
        "Дедлайн": {"date": {"start": deadline} if deadline else None},
        "Напоминание": {"date": {"start": reminder} if reminder else None},
    }
    return props


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
    props = _make_task_props(deadline=None, reminder="2026-05-08T16:00:00+03:00")

    update_calls: list = []

    async def capture_update(task_id, props_dict):
        update_calls.append((task_id, props_dict))

    with patch.object(tasks, "update_page", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-1", props, "Ежедневно", "тестовая задача", uid=999_001,
        )

    assert update_calls, "update_page должен быть вызван"
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
    props = _make_task_props(
        deadline="2026-06-01",
        reminder="2026-06-01T09:00:00+03:00",
    )

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks, "update_page", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-2", props, "Ежедневно", "test task", uid=999_001,
        )

    assert captured["props"]["Статус"]["status"]["name"] == "In progress"
    assert "Время завершения" in captured["props"]
    # Дедлайн сдвинут (был 2026-06-01, должен стать позже)
    assert "Дедлайн" in captured["props"]


@pytest.mark.asyncio
async def test_recurring_reminder_done_already_in_progress():
    """_handle_recurring_reminder_done должен ставить In progress (legacy путь)."""
    from nexus.handlers import tasks

    msg = _make_message()

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks, "update_page", AsyncMock(side_effect=capture_update)):
        await tasks._handle_recurring_reminder_done(msg, "task-id-3", "test task")

    assert captured["props"]["Статус"]["status"]["name"] == "In progress"


@pytest.mark.asyncio
async def test_completion_timestamp_format_moscow_tz():
    """Время завершения должно быть в Moscow tz (+03:00) и валидным ISO."""
    from nexus.handlers import tasks

    msg = _make_message()
    props = _make_task_props(deadline=None, reminder=None)

    captured: dict = {}

    async def capture_update(task_id, props_dict):
        captured["props"] = props_dict

    with patch.object(tasks, "update_page", AsyncMock(side_effect=capture_update)), \
         patch.object(tasks, "_scheduler", None), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)):
        await tasks._handle_recurring_task_reset(
            msg, "task-id-4", props, "Ежедневно", "x", uid=999_001,
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
                    cat="🏠 Ж***", deadline=None, reminder=None,
                    repeat_time="", repeat=None, completed=None):
    return {
        "id": task_id,
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": prio}},
            "Категория": {"select": {"name": cat}},
            "Дедлайн": {"date": {"start": deadline} if deadline else None},
            "Напоминание": {"date": {"start": reminder} if reminder else None},
            "Время повтора": {
                "rich_text": [{"plain_text": repeat_time}] if repeat_time else []
            },
            "Повтор": {"select": {"name": repeat} if repeat else None},
            "Время завершения": {
                "date": {"start": completed} if completed else None
            },
        },
    }


def _build_qp_mock(tasks):
    """Минимальный диспатч query_pages: на любой filter возвращаем tasks."""
    import json as _json

    async def _qp(db_id, *, filters=None, **kwargs):
        f_str = _json.dumps(filters or {}, ensure_ascii=False)
        if '"Тип"' in f_str and "Расход" in f_str:
            return []
        if '"Категория"' in f_str and "СДВГ" in f_str:
            return []
        if '"Статус"' in f_str:
            return tasks
        return []
    return _qp


def _today_get_response(client, tasks):
    qp_mock = _build_qp_mock(tasks)
    today_date = _today_local_date(3)
    with patch("miniapp.backend.routes.today.query_pages", side_effect=qp_mock), \
         patch("miniapp.backend.routes.today.memory_get",
               AsyncMock(return_value=None)), \
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
