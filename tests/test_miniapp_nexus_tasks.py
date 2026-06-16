"""Mini App — Nexus: задачи, календарь, стрики, погода, дайджест.

GET /api/tasks (active/today/overdue/done, closed tasks), POST /api/tasks*
(done/postpone/cancel/create), /api/calendar, /api/streaks, /api/weather,
401-проверки write-endpoint'ов, дайджест /today бота.

Собрано из wave2a / wave3 / wave5 / wave6 / wave8.62 при реорганизации
тестов по доменам.
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
from nexus.repos.pg_tasks_repo import Task as PgTask


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


# ── helpers: fake PG Task objects ────────────────────────────────────────────

def _pg_task(task_id, title, *, status="Not started", priority="🔴 Срочно",
             category="🐾 Коты", deadline="", reminder="",
             repeat_time="", repeat="Нет",
             completed_at="", last_edited="",
             user_notion_id=FAKE_NOTION_USER):
    return PgTask(
        id=task_id,
        title=title,
        status=status,
        priority=priority,
        category=category,
        deadline=deadline,
        reminder=reminder,
        repeat_time=repeat_time,
        repeat=repeat,
        completed_at=completed_at,
        last_edited=last_edited,
        user_notion_id=user_notion_id,
    )


# ── helpers: fake Notion pages (used by calendar/today which still use Notion) ─

def _task(task_id, title, *, status="Not started", prio="🔴 Срочно",
          cat="🐾 Коты", deadline=None, reminder=None,
          repeat_time="", repeat=None, bot="☀️ Nexus",
          completion=None, last_edited=None):
    page = {
        "id": task_id,
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": prio}},
            "Категория": {"select": {"name": cat}},
            "Бот": {"select": {"name": bot}},
            "Дедлайн": {"date": {"start": deadline} if deadline else None},
            "Напоминание": {"date": {"start": reminder} if reminder else None},
            "Время повтора": {"rich_text": [{"plain_text": repeat_time}] if repeat_time else []},
            "Повтор": {"select": {"name": repeat} if repeat else None},
            "Время завершения": {"date": {"start": completion} if completion else None},
        },
    }
    if last_edited:
        page["last_edited_time"] = last_edited
    return page


# ── GET /api/tasks ───────────────────────────────────────────────────────────

def test_tasks_active_filters_and_sorts(client):
    tz = 3
    today = _today_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

    tasks = [
        _pg_task("a", "Активная 1", priority="🟡 Важно",
                 deadline=f"{tomorrow}T00:00:00+00:00"),
        _pg_task("b", "Срочная сегодня", priority="🔴 Срочно",
                 deadline=f"{today}T00:00:00+00:00"),
        _pg_task("c", "Потом", priority="⚪ Можно потом",
                 deadline=f"{tomorrow}T00:00:00+00:00"),
        # Просрочка — хоть статус активный, не попадает в active
        _pg_task("d", "Просрочена", priority="🔴 Срочно",
                 deadline=f"{yesterday}T00:00:00+00:00"),
    ]

    with patch("miniapp.backend.routes.tasks._tasks_repo.active",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=active")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["filter"] == "active"
    ids = [t["id"] for t in data["tasks"]]
    # d исключена (overdue), b первой (🔴), затем 🟡 a, потом ⚪ c
    assert ids == ["b", "a", "c"]
    assert data["tasks"][0]["prio"] == "🔴"
    assert data["tasks"][0]["cat"] == {"emoji": "🐾", "name": "Коты", "full": "🐾 Коты"}
    assert data["tasks"][0]["streak"] is None


def test_tasks_overdue_filter(client):
    tz = 3
    today = _today_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")

    tasks = [_pg_task("x", "Просрочка",
                      deadline=f"{yesterday}T00:00:00+00:00",
                      priority="🔴 Срочно")]

    with patch("miniapp.backend.routes.tasks._tasks_repo.active",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=overdue")

    assert r.status_code == 200
    data = r.json()
    assert data["tasks"][0]["status"] == "overdue"


def test_tasks_invalid_filter(client):
    r = client.get("/api/tasks?filter=bogus")
    assert r.status_code == 400


def test_tasks_empty(client):
    tz = 3
    with patch("miniapp.backend.routes.tasks._tasks_repo.active",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/tasks")
    assert r.status_code == 200
    assert r.json() == {"filter": "active", "total": 0, "tasks": []}


def test_tasks_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/tasks").status_code == 401


def test_tasks_filter_does_not_include_bot_property(client):
    """PG-путь: нет фильтра 'Бот' — база задач Nexus-only."""
    tz = 3
    today = _today_iso(tz)
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    tasks = [_pg_task("ok", "Активная", deadline=f"{tomorrow}T00:00:00+00:00")]

    with patch("miniapp.backend.routes.tasks._tasks_repo.active",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=active")

    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["tasks"]]
    assert "ok" in ids


def test_tasks_filter_today_returns_only_today_and_overdue(client):
    tz = 3
    today = _today_iso(tz)
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")

    tasks = [
        _pg_task("overdue-1", "Просрочена", priority="🔴 Срочно",
                 deadline=f"{yesterday}T00:00:00+00:00"),
        _pg_task("today-1", "Сегодня", priority="🟡 Важно",
                 deadline=f"{today}T00:00:00+00:00"),
        _pg_task("future-1", "Потом", priority="🟡 Важно",
                 deadline=f"{tomorrow}T00:00:00+00:00"),
        _pg_task("done-1", "Готово", priority="🔴 Срочно",
                 deadline=f"{today}T00:00:00+00:00", status="Done"),
    ]

    with patch("miniapp.backend.routes.tasks._tasks_repo.active",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=today")

    assert r.status_code == 200
    data = r.json()
    ids = {t["id"] for t in data["tasks"]}
    assert ids == {"overdue-1", "today-1"}


# ── GET /api/tasks — закрытые/отменённые (wave8.62) ─────────────────────────

def test_archived_task_serialized_as_cancelled_with_closed_at(client):
    tz = 3
    tasks = [
        _pg_task("a1", "Отменённая",
                 status="Archived",
                 completed_at="2026-04-28T10:00:00+03:00"),
    ]

    with patch("miniapp.backend.routes.tasks._tasks_repo.list_all",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=done")

    assert r.status_code == 200, r.text
    items = r.json()["tasks"]
    assert len(items) == 1
    assert items[0]["status"] == "cancelled"
    assert items[0]["closed_at"] == "2026-04-28T10:00:00+03:00"


def test_closed_at_falls_back_to_last_edited_time(client):
    tz = 3
    tasks = [
        _pg_task("a2", "Отменённая без completion",
                 status="Archived",
                 last_edited="2026-04-15T08:30:00+00:00"),
    ]

    with patch("miniapp.backend.routes.tasks._tasks_repo.list_all",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=done")

    assert r.status_code == 200
    items = r.json()["tasks"]
    assert len(items) == 1
    assert items[0]["status"] == "cancelled"
    assert items[0]["closed_at"] == "2026-04-15T08:30:00+00:00"


def test_active_filter_excludes_archived(client):
    tz = 3
    today = _today_date(tz)
    tomorrow = (today + timedelta(days=2)).isoformat()

    tasks = [
        _pg_task("ok", "Активная", deadline=f"{tomorrow}T00:00:00+00:00",
                 priority="🔴 Срочно"),
        _pg_task("arc", "Отменённая",
                 status="Archived", deadline=f"{tomorrow}T00:00:00+00:00",
                 priority="🔴 Срочно",
                 completed_at="2026-04-20T10:00:00+03:00"),
    ]

    with patch("miniapp.backend.routes.tasks._tasks_repo.active",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=active")

    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["tasks"]]
    # client-side фильтр отрезает cancelled (Archived → status=cancelled)
    assert ids == ["ok"]


def test_done_filter_includes_done_and_archived_sorted_by_closed_at_desc(client):
    tz = 3
    tasks = [
        _pg_task("old-done", "Старая выполненная",
                 status="Done",
                 completed_at="2026-03-01T10:00:00+03:00"),
        _pg_task("recent-cancel", "Свежая отменённая",
                 status="Archived",
                 completed_at="2026-04-20T10:00:00+03:00"),
        _pg_task("mid-done", "Средняя выполненная",
                 status="Complete",
                 completed_at="2026-04-10T10:00:00+03:00"),
    ]

    with patch("miniapp.backend.routes.tasks._tasks_repo.list_all",
               AsyncMock(return_value=tasks)), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=done")

    assert r.status_code == 200
    result = r.json()["tasks"]
    ids = [t["id"] for t in result]
    assert ids == ["recent-cancel", "mid-done", "old-done"]
    statuses = {t["id"]: t["status"] for t in result}
    assert statuses["recent-cancel"] == "cancelled"
    assert statuses["mid-done"] == "done"
    assert statuses["old-done"] == "done"


# ── POST /api/tasks/{id}/done ────────────────────────────────────────────────

def test_task_done_updates_status(client):
    # CRITICAL: мокать update_streak. Иначе тест дёргает реальный
    # nexus.handlers.streaks.update_streak, который пишет в prod-файл
    # data/nexus_streaks.db под FAKE_TG_ID=67686090 (= реальный tg Кай).
    # См. issue #65 — это и есть «стрик без Done» из обследования.
    task = _pg_task("task-1", "Test", user_notion_id=FAKE_NOTION_USER)
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
               AsyncMock(return_value=True)) as upd, \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_props",
               AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("nexus.handlers.streaks.update_streak", AsyncMock(return_value=None)):
        r = client.post("/api/tasks/task-1/done")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "Done"}
    upd.assert_awaited_once_with("task-1", "Done")


def test_task_done_rejects_stranger(client):
    task = _pg_task("task-2", "Test", user_notion_id="not-my-user")
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/task-2/done")
    assert r.status_code == 404


def test_task_done_404_when_page_missing(client):
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/missing-id/done")
    assert r.status_code == 404


# ── POST /api/tasks/{id}/postpone ────────────────────────────────────────────

def test_task_postpone_shifts_date(client):
    tz = 3
    today = _today_date(tz)
    task = _pg_task("t-3", "Test", deadline=today.isoformat() + "T00:00:00+00:00",
                    user_notion_id=FAKE_NOTION_USER)
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_props",
               AsyncMock(return_value=None)) as upd, \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/t-3/postpone", json={"days": 2})
    assert r.status_code == 200
    expected = (today + timedelta(days=2)).isoformat()
    assert r.json()["new_date"] == expected
    # первый вызов set_props — дедлайн
    first_call = upd.await_args_list[0]
    assert first_call.args[0] == "t-3"
    assert first_call.args[1]["Дедлайн"]["date"]["start"] == expected


def test_task_postpone_falls_back_to_today_when_no_deadline(client):
    tz = 3
    today = _today_date(tz)
    task = _pg_task("t-4", "Test", deadline="", user_notion_id="")
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_props",
               AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.post("/api/tasks/t-4/postpone", json={"days": 5})
    assert r.status_code == 200
    assert r.json()["new_date"] == (today + timedelta(days=5)).isoformat()


# ── POST /api/tasks/{id}/cancel ──────────────────────────────────────────────

def test_task_cancel_sets_archived(client):
    task = _pg_task("t-5", "Test", user_notion_id=FAKE_NOTION_USER)
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
               AsyncMock(return_value=True)) as upd, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/t-5/cancel")
    assert r.status_code == 200
    upd.assert_awaited_once_with("t-5", "Archived")


# ── POST /api/tasks (create) ─────────────────────────────────────────────────

def test_task_create_minimal(client):
    with patch("miniapp.backend.routes.writes._tasks_pg_repo.create",
               AsyncMock(return_value="42")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks", json={"title": "Купить молоко"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "42"}
    args, _ = pc.await_args
    _, props = args
    assert props["Задача"]["title"][0]["text"]["content"] == "Купить молоко"
    assert props["Статус"]["status"]["name"] == "Not started"


# ── /api/calendar ────────────────────────────────────────────────────────────

def test_calendar_groups_tasks_by_day(client):
    tz = 3
    today = _today_date(tz)
    month = today.strftime("%Y-%m")
    d22 = today.replace(day=22).isoformat()
    d15 = today.replace(day=15).isoformat()

    pages = [
        _task("a", "Лоток", deadline=d22, prio="🟡 Важно"),
        _task("b", "Счёт", deadline=d22, prio="🔴 Срочно"),
        _task("c", "Тренажёрка", deadline=d15, prio="⚪ Можно потом"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.calendar.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.calendar.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.calendar.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get(f"/api/calendar?month={month}")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["month"] == month
    d22_bucket = data["days"]["22"]
    assert d22_bucket["count"] == 2
    assert d22_bucket["has_high_prio"] is True
    assert {t["id"] for t in d22_bucket["tasks"]} == {"a", "b"}
    d1_bucket = data["days"]["1"]
    assert d1_bucket == {"count": 0, "has_overdue": False,
                         "has_high_prio": False, "tasks": []}


def test_calendar_defaults_to_current_month(client):
    tz = 3
    today = _today_date(tz)

    async def qp(*_, **__):
        return []

    with patch("miniapp.backend.routes.calendar.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.calendar.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.calendar.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.get("/api/calendar")

    assert r.status_code == 200
    assert r.json()["month"] == today.strftime("%Y-%m")


def test_calendar_401_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    assert c.get("/api/calendar").status_code == 401


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


# ── /api/today: запрос задач без фильтра «Бот» ───────────────────────────────

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


# ── /today (nexus bot) — не обрывается ───────────────────────────────────────

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


# ── /api/streaks ─────────────────────────────────────────────────────────────

def test_streaks_endpoint_returns_current_and_best(client):
    with patch("nexus.handlers.streaks.get_streak",
               return_value={"streak": 12, "best": 30, "last_activity_date": "2026-04-21",
                             "rest_day_date": None, "rest_days_used": 0,
                             "streak_start_date": "2026-04-10"}), \
         patch("nexus.handlers.streaks.is_rest_day_available", return_value=True):
        r = client.get("/api/streaks")
    assert r.status_code == 200
    data = r.json()
    assert data["current"] == 12
    assert data["best"] == 30
    assert data["rest_day_available"] is True
    assert "per_task" in data


def test_streaks_week_returns_7_days(client):
    with patch("miniapp.backend.routes.streaks.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.streaks._has_activity_on", return_value=False):
        r = client.get("/api/streaks/week")
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 7
    # последний день — сегодня
    assert days[-1]["is_today"] is True
    # все имеют weekday
    for d in days:
        assert d["weekday"] in ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


# ── /api/weather ─────────────────────────────────────────────────────────────

def test_weather_route_is_registered():
    """hotfix: /api/weather должен быть в списке роутов FastAPI app."""
    from miniapp.backend.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/weather" in paths


def test_weather_returns_cached_or_fetches(client, tmp_path, monkeypatch):
    """Cache ключ — tg_id; при первом запросе — вызов Open-Meteo; при повторном — из кэша."""

    # direct in-test call: fake tz + fake openmeteo
    async def fake_memory_get(key):
        return "Europe/Moscow" if key.startswith("tz_") else None

    fetch_call_count = {"n": 0}

    async def fake_fetch(city):
        fetch_call_count["n"] += 1
        return {"city": city, "temp": 12, "code": 0, "kind": "clear", "description": "Ясно"}

    with patch("miniapp.backend.routes.weather.memory_get", side_effect=fake_memory_get), \
         patch("miniapp.backend.routes.weather._fetch_openmeteo", side_effect=fake_fetch):
        r1 = client.get("/api/weather")
        r2 = client.get("/api/weather")

    assert r1.status_code == 200
    assert r1.json()["city"] == "Moscow"
    assert r1.json()["temp"] == 12
    assert r1.json()["kind"] == "clear"
    # второй запрос — из кэша (не второй fetch)
    assert fetch_call_count["n"] == 1


# ── 401 для всех POST endpoints ──────────────────────────────────────────────

def test_writes_reject_without_init_data():
    app.dependency_overrides.clear()
    c = TestClient(app)
    paths = [
        ("/api/tasks/x/done", {}),
        ("/api/tasks/x/postpone", {"days": 1}),
        ("/api/tasks/x/cancel", {}),
        ("/api/tasks", {"title": "x"}),
        ("/api/expenses", {"amount": 100, "cat": "🍜 Продукты"}),
        ("/api/arcana/sessions/s/verify", {"status": "✅ Да"}),
        ("/api/arcana/rituals/r/result", {"status": "✅ Сработало"}),
        ("/api/arcana/clients", {"name": "X"}),
        ("/api/lists", {"type": "buy", "name": "X"}),
        ("/api/lists/l/done", {}),
        ("/api/lists/l/delete", {}),
        ("/api/memory", {"text": "X"}),
    ]
    for path, body in paths:
        r = c.post(path, json=body)
        assert r.status_code == 401, f"{path} → {r.status_code}"
