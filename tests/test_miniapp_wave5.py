"""Wave 5 tests — critical fixes + feature additions.

Stage 1: фильтр "Бот" убран из задач/календаря/today, filter=today работает.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend import cache
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    db_file = tmp_path / "adhd_cache.db"
    monkeypatch.setattr(cache, "_DB_PATH", str(db_file))
    cache._init_db()
    yield


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _today_iso(tz: int = 3) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).strftime("%Y-%m-%d")


def _today_date(tz: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


def _task(task_id, title, *, status="Not started", prio="🔴 Срочно",
          cat="🐾 Коты", deadline=None, reminder=None,
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


# ─── Tasks filter не содержит "Бот" ─────────────────────────────────────────

def test_tasks_filter_does_not_include_bot_property(client):
    """База задач — Nexus-only, фильтр 'Бот' у Notion вызывает 400."""
    captured = {}

    async def qp(db_id, *, filters=None, **kwargs):
        captured["filters"] = filters
        return []

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=active")

    assert r.status_code == 200
    filter_str = _json.dumps(captured["filters"] or {}, ensure_ascii=False)
    assert '"Бот"' not in filter_str, f"Filter should not include 'Бот': {filter_str}"


def test_calendar_filter_does_not_include_bot(client):
    captured = {}

    async def qp(db_id, *, filters=None, **kwargs):
        captured["filters"] = filters
        return []

    with patch("miniapp.backend.routes.calendar.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.calendar.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.calendar.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/calendar?month=2026-04")

    assert r.status_code == 200
    filter_str = _json.dumps(captured["filters"] or {}, ensure_ascii=False)
    assert '"Бот"' not in filter_str


def test_today_task_fetch_does_not_include_bot(client):
    """GET /api/today запрос задач не содержит фильтра по 'Бот'."""
    captured_filters = []

    async def qp(db_id, *, filters=None, **kwargs):
        captured_filters.append(_json.dumps(filters or {}, ensure_ascii=False))
        return []

    with patch("miniapp.backend.routes.today.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.today.memory_get", AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.today.ask_claude", AsyncMock(return_value="tip")), \
         patch("miniapp.backend.routes.today.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 0, "best": 0, "last_activity_date": None,
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": None}), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=False):
        r = client.get("/api/today")

    assert r.status_code == 200
    # Task fetch filter shouldn't mention "Бот"
    task_filter = next(f for f in captured_filters if '"Статус"' in f and '"Расход"' not in f)
    assert '"Бот"' not in task_filter


# ─── filter=today ────────────────────────────────────────────────────────────

def test_tasks_filter_today_returns_only_today_and_overdue(client):
    tz = 3
    today = _today_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

    pages = [
        _task("overdue-1", "Просрочена", prio="🔴 Срочно", deadline=yesterday),
        _task("today-1", "Сегодня", prio="🟡 Важно", deadline=today),
        _task("future-1", "Потом", prio="🟡 Важно", deadline=tomorrow),
        _task("done-1", "Готово", prio="🔴 Срочно", deadline=today, status="Done"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=today")

    assert r.status_code == 200
    data = r.json()
    ids = {t["id"] for t in data["tasks"]}
    assert ids == {"overdue-1", "today-1"}


# ─── /today (nexus bot) — не обрывается ─────────────────────────────────────

@pytest.mark.asyncio
async def test_nexus_today_digest_complete_ending():
    """Дайджест /today не должен обрываться на многоточие / полслова."""
    from nexus.handlers.tasks import _build_today_digest

    # Полноценный совет, не обрезанный.
    fake_advice = (
        "Начни с самой простой — налить воду в лоток займёт 2 минуты, "
        "и ты справишься без проблем."
    )

    async def fake_query_pages(*args, **kwargs):
        return []

    with patch("core.notion_client.query_pages", AsyncMock(side_effect=fake_query_pages)), \
         patch("core.notion_client.db_query", AsyncMock(return_value=[])), \
         patch("core.notion_client.memory_get", AsyncMock(return_value=None)), \
         patch("nexus.handlers.tasks.ask_claude", AsyncMock(return_value=fake_advice)), \
         patch("nexus.handlers.tasks._get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 5, "best": 10, "last_activity_date": None,
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": None}), \
         patch("nexus.handlers.finance._calc_free_remaining",
               AsyncMock(return_value=None)):
        text = await _build_today_digest(uid=FAKE_TG_ID, user_notion_id=FAKE_NOTION_USER)

    assert len(text) > 100, f"digest too short: {len(text)} chars"
    # не заканчивается на многоточие или полслово
    last_char = text.rstrip()[-1]
    valid_ends = {".", "!", "?", ")", "»", "]", "'", "\""}
    is_emoji = ord(last_char) > 127 and not last_char.isalnum()
    assert last_char in valid_ends or is_emoji, (
        f"digest ends on unexpected char {last_char!r}; tail: {text[-50:]!r}"
    )
    assert not text.rstrip().endswith("..."), "digest обрывается на многоточии"


# ═════════════════════════════════════════════════════════════════════════════
# Stage 2: POST /api/finance (income/expense/practice_income) + /api/categories
# ═════════════════════════════════════════════════════════════════════════════

def test_finance_post_expense_routes_to_finance_add(client):
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "new-page-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "expense",
            "amount": 500,
            "cat": "🍜 Продукты",
            "desc": "Магнит",
        })

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["type"] == "expense"
    assert captured["type_"] == "💸 Расход"
    assert captured["category"] == "🍜 Продукты"
    assert captured["amount"] == 500
    assert captured["bot_label"] == "☀️ Nexus"


def test_finance_post_income_default_category(client):
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "inc-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "income",
            "amount": 80000,
            "desc": "зарплата",
        })

    assert r.status_code == 200, r.text
    assert captured["type_"] == "💰 Доход"
    assert captured["category"] == "🏦 Прочее"  # дефолт когда cat не указан
    assert captured["amount"] == 80000


def test_finance_post_practice_income_forces_arcana(client):
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "practice-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "practice_income",
            "amount": 3500,
            "desc": "клиент Анна",
            "bot": "nexus",  # игнорируется, практика всегда Arcana
        })

    assert r.status_code == 200, r.text
    assert captured["bot_label"] == "🌒 Arcana"
    assert captured["type_"] == "💰 Доход"


def test_finance_expense_requires_category(client):
    with patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/finance", json={
            "type": "expense",
            "amount": 100,
        })
    assert r.status_code == 400
    assert "cat is required" in r.json()["detail"]


def test_expenses_alias_still_works(client):
    """Deprecated /api/expenses всё ещё работает через finance_create."""
    captured = {}

    async def fake_finance_add(**kwargs):
        captured.update(kwargs)
        return "legacy-id"

    with patch("miniapp.backend.routes.writes.finance_add", side_effect=fake_finance_add), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/expenses", json={
            "amount": 200, "cat": "🍜 Продукты", "desc": "test",
        })

    assert r.status_code == 200
    assert r.json()["type"] == "expense"
    assert captured["type_"] == "💸 Расход"


def test_categories_task_returns_merged_list(client):
    """GET /api/categories?type=task возвращает существующие + дефолты."""
    pages = [
        {"id": "t1", "properties": {"Категория": {"select": {"name": "🐾 Коты"}}}},
        {"id": "t2", "properties": {"Категория": {"select": {"name": "🐾 Коты"}}}},
        {"id": "t3", "properties": {"Категория": {"select": {"name": "💜 Люди"}}}},
        # запись без категории
        {"id": "t4", "properties": {"Категория": {"select": None}}},
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.categories.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.categories.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/categories?type=task")

    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "task"
    assert "🐾 Коты" in data["categories"]
    assert "💜 Люди" in data["categories"]
    # дефолты тоже добавились
    assert "🏠 Дом" in data["categories"]
    assert "💼 Работа" in data["categories"]
    # без дублирования
    assert data["categories"].count("🐾 Коты") == 1


def test_categories_invalid_type(client):
    r = client.get("/api/categories?type=bogus")
    assert r.status_code == 400


def test_categories_income_returns_defaults_when_empty(client):
    async def qp(*_, **__):
        return []

    with patch("miniapp.backend.routes.categories.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.categories.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/categories?type=income")

    assert r.status_code == 200
    cats = r.json()["categories"]
    assert "💼 Зарплата" in cats
    assert "🏦 Прочее" in cats
