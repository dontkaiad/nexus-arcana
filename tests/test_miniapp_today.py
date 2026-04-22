"""Tests for GET /api/today — Mini App backend wave 1."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes.today import first_emoji


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Redirect adhd cache to a tmp SQLite file per test."""
    db_file = tmp_path / "adhd_cache.db"
    monkeypatch.setattr(cache, "_DB_PATH", str(db_file))
    cache._init_db()
    yield


@pytest.fixture
def client():
    """TestClient with auth dep overridden to return a fixed tg_id."""
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _today_local_iso(tz_offset: int = 3) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).strftime("%Y-%m-%d")


def _today_local_date(tz_offset: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).date()


def _make_task(task_id, title, *, status="Not started", prio="🔴 Срочно",
               cat="🏥 Здоровье", deadline=None, reminder=None,
               repeat_time="", repeat=None):
    return {
        "id": task_id,
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": prio}},
            "Категория": {"select": {"name": cat}},
            "Дедлайн": {"date": {"start": deadline} if deadline else None},
            "Напоминание": {"date": {"start": reminder} if reminder else None},
            "Время повтора": {"rich_text": [{"plain_text": repeat_time}] if repeat_time else []},
            "Повтор": {"select": {"name": repeat} if repeat else None},
        },
    }


def _make_expense(amount: float):
    return {"id": "fin-1", "properties": {"Сумма": {"number": amount}}}


def _build_query_pages_mock(tasks, expenses, memories):
    """Router: dispatch query_pages call by filter content.

    (В conftest все NOTION_DB_* = "fake-db-id", поэтому по db_id различить
    нельзя — опираемся на характерные поля фильтра.)
    """
    import json as _json

    async def _qp(db_id, *, filters=None, **kwargs):
        f_str = _json.dumps(filters or {}, ensure_ascii=False)
        if '"Тип"' in f_str and "Расход" in f_str:
            return expenses
        if '"Категория"' in f_str and "СДВГ" in f_str:
            return memories
        if '"Статус"' in f_str:
            return tasks
        return []
    return _qp


# ─── first_emoji ──────────────────────────────────────────────────────────────

def test_first_emoji_picks_symbol():
    assert first_emoji("🔴 Срочно") == "🔴"
    assert first_emoji("💻 Подписки") == "💻"
    assert first_emoji("⚪ Можно потом") == "⚪"


def test_first_emoji_returns_empty_when_no_emoji():
    assert first_emoji("Срочно") == ""
    assert first_emoji("") == ""


# ─── /api/today ───────────────────────────────────────────────────────────────

def test_today_returns_all_keys_and_classifies_tasks(client):
    tz = 3
    today = _today_local_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

    tasks = [
        # overdue — deadline in the past
        _make_task("t-overdue", "Налоги", deadline=yesterday, cat="💳 Прочее",
                   prio="🟡 Важно"),
        # scheduled — deadline today with time + reminder 60 min before
        _make_task("t-sched", "Врач",
                   deadline=f"{today}T09:00:00+03:00",
                   reminder=f"{today}T08:00:00+03:00",
                   cat="🏥 Здоровье", prio="🔴 Срочно"),
        # scheduled — repeating task with repeat_time
        _make_task("t-repeat", "Витамины",
                   deadline=None, repeat_time="08:30", repeat="Ежедневно",
                   cat="🏥 Здоровье", prio="🟡 Важно"),
        # today without time — goes to tasks
        _make_task("t-today", "Разобрать лоток",
                   deadline=today, cat="🏠 Жилье", prio="⚪ Можно потом"),
        # future — goes to tasks
        _make_task("t-future", "Отправить счёт",
                   deadline=tomorrow, cat="💻 Подписки", prio="🟡 Важно"),
    ]
    expenses = [_make_expense(1500), _make_expense(1104)]

    qp_mock = _build_query_pages_mock(tasks, expenses, [])
    claude_mock = AsyncMock(return_value="Начни с лотка — 2 минуты.")

    with patch("miniapp.backend.routes.today.query_pages", side_effect=qp_mock), \
         patch("miniapp.backend.routes.today.memory_get", AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.today.ask_claude", claude_mock), \
         patch("miniapp.backend.routes.today.today_user_tz", AsyncMock(return_value=(_today_local_date(tz), tz))), \
         patch("miniapp.backend.routes.today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 8, "best": 12, "last_activity_date": today,
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": today}), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=True):
        resp = client.get("/api/today")

    assert resp.status_code == 200, resp.text
    data = resp.json()

    # top-level keys
    for key in ("date", "weekday", "tz_offset", "streak", "budget",
                "overdue", "scheduled", "tasks", "adhd_tip"):
        assert key in data, f"missing key: {key}"

    assert data["date"] == today
    assert data["tz_offset"] == tz
    assert data["streak"] == {"current": 8, "rest_day_available": True}

    # budget: default 4166, spent 1500+1104=2604
    assert data["budget"]["day"] == 4166
    assert data["budget"]["spent_today"] == 2604
    assert data["budget"]["left"] == 4166 - 2604
    assert data["budget"]["pct"] == round(2604 / 4166 * 100)

    # classification
    assert len(data["overdue"]) == 1
    assert data["overdue"][0]["id"] == "t-overdue"
    assert data["overdue"][0]["cat"] == "💳"
    assert data["overdue"][0]["prio"] == "🟡"
    assert data["overdue"][0]["days_ago"] == 2

    sched_ids = {s["id"] for s in data["scheduled"]}
    assert sched_ids == {"t-sched", "t-repeat"}
    doc = next(s for s in data["scheduled"] if s["id"] == "t-sched")
    assert doc["time"] == "09:00"
    assert doc["reminder_min"] == 60
    vit = next(s for s in data["scheduled"] if s["id"] == "t-repeat")
    assert vit["time"] == "08:30"
    assert vit["reminder_min"] is None
    assert vit["repeat"] == "Ежедневно"

    task_ids = [t["id"] for t in data["tasks"]]
    assert "t-today" in task_ids
    assert "t-future" in task_ids
    # today-without-time перед future
    assert task_ids.index("t-today") < task_ids.index("t-future")

    assert data["adhd_tip"] == "Начни с лотка — 2 минуты."
    assert claude_mock.await_count == 1


def test_today_caches_adhd_tip_across_calls(client):
    tz = 3
    today = _today_local_iso(tz)
    qp_mock = _build_query_pages_mock([], [], [])
    claude_mock = AsyncMock(return_value="Дыши, ты справишься.")

    with patch("miniapp.backend.routes.today.query_pages", side_effect=qp_mock), \
         patch("miniapp.backend.routes.today.memory_get", AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.today.ask_claude", claude_mock), \
         patch("miniapp.backend.routes.today.today_user_tz", AsyncMock(return_value=(_today_local_date(tz), tz))), \
         patch("miniapp.backend.routes.today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 0, "best": 0, "last_activity_date": None,
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": None}), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=False):
        r1 = client.get("/api/today")
        r2 = client.get("/api/today")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["adhd_tip"] == "Дыши, ты справишься."
    assert r2.json()["adhd_tip"] == "Дыши, ты справишься."
    # второй вызов обслужен из кэша — Claude не вызвался повторно
    assert claude_mock.await_count == 1


def test_today_uses_custom_budget_from_memory(client):
    tz = 3
    qp_mock = _build_query_pages_mock([], [_make_expense(600)], [])

    with patch("miniapp.backend.routes.today.query_pages", side_effect=qp_mock), \
         patch("miniapp.backend.routes.today.memory_get",
               AsyncMock(return_value="5000")), \
         patch("miniapp.backend.routes.today.ask_claude",
               AsyncMock(return_value="tip")), \
         patch("miniapp.backend.routes.today.today_user_tz", AsyncMock(return_value=(_today_local_date(tz), tz))), \
         patch("miniapp.backend.routes.today.get_user_notion_id",
               AsyncMock(return_value="")), \
         patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 0, "best": 0, "last_activity_date": None,
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": None}), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=False):
        resp = client.get("/api/today")

    assert resp.status_code == 200
    assert resp.json()["budget"]["day"] == 5000
    assert resp.json()["budget"]["spent_today"] == 600
    assert resp.json()["budget"]["left"] == 4400


def test_today_rejects_missing_init_data():
    """Без X-Telegram-Init-Data — 401 (dep_override не ставим)."""
    app.dependency_overrides.clear()
    c = TestClient(app)
    resp = c.get("/api/today")
    assert resp.status_code == 401
