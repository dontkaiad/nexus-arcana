"""wave8.62 — закрытые/отменённые задачи + чеклисты родителей-Done в Mini App."""
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


def _today_date(tz: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


def _task(task_id, title, *, status="Not started", prio="🔴 Срочно",
          cat="🐾 Коты", deadline=None, completion=None, last_edited=None,
          bot="☀️ Nexus"):
    page = {
        "id": task_id,
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": prio}},
            "Категория": {"select": {"name": cat}},
            "Бот": {"select": {"name": bot}},
            "Дедлайн": {"date": {"start": deadline} if deadline else None},
            "Напоминание": {"date": None},
            "Время повтора": {"rich_text": []},
            "Повтор": {"select": None},
            "Время завершения": {"date": {"start": completion} if completion else None},
        },
    }
    if last_edited:
        page["last_edited_time"] = last_edited
    return page


# ── Bug 1 ──────────────────────────────────────────────────────────────────

def test_archived_task_serialized_as_cancelled_with_closed_at(client):
    tz = 3
    pages = [
        _task("a1", "Отменённая",
              status="Archived",
              completion="2026-04-28T10:00:00.000+03:00"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=done")

    assert r.status_code == 200, r.text
    tasks = r.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "cancelled"
    assert tasks[0]["closed_at"] == "2026-04-28T10:00:00.000+03:00"


def test_closed_at_falls_back_to_last_edited_time(client):
    tz = 3
    pages = [
        _task("a2", "Отменённая без completion",
              status="Archived",
              last_edited="2026-04-15T08:30:00.000Z"),
    ]

    async def qp(*_, **__):
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=done")

    assert r.status_code == 200
    tasks = r.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "cancelled"
    assert tasks[0]["closed_at"] == "2026-04-15T08:30:00.000Z"


def test_active_filter_excludes_archived(client):
    tz = 3
    captured = {}
    today = _today_date(tz)
    tomorrow = (today + timedelta(days=2)).isoformat()

    pages = [
        _task("ok", "Активная", deadline=tomorrow, prio="🔴 Срочно"),
        _task("arc", "Отменённая",
              status="Archived", deadline=tomorrow, prio="🔴 Срочно",
              completion="2026-04-20T10:00:00.000+03:00"),
    ]

    async def qp(_db, *, filters=None, **__):
        captured["filters"] = filters
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=active")

    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["tasks"]]
    # client-side фильтр отрезает cancelled (Archived → status=cancelled)
    assert ids == ["ok"]
    # И в Notion-фильтре есть does_not_equal Archived
    f_str = _json.dumps(captured["filters"], ensure_ascii=False)
    assert "Archived" in f_str
    assert "does_not_equal" in f_str


def test_done_filter_includes_done_and_archived_sorted_by_closed_at_desc(client):
    tz = 3
    pages = [
        _task("old-done", "Старая выполненная",
              status="Done",
              completion="2026-03-01T10:00:00.000+03:00"),
        _task("recent-cancel", "Свежая отменённая",
              status="Archived",
              completion="2026-04-20T10:00:00.000+03:00"),
        _task("mid-done", "Средняя выполненная",
              status="Complete",
              completion="2026-04-10T10:00:00.000+03:00"),
    ]
    captured = {}

    async def qp(_db, *, filters=None, **__):
        captured["filters"] = filters
        return pages

    with patch("miniapp.backend.routes.tasks.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.tasks.today_user_tz",
               AsyncMock(return_value=(_today_date(tz), tz))), \
         patch("miniapp.backend.routes.tasks.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.get("/api/tasks?filter=done")

    assert r.status_code == 200
    tasks = r.json()["tasks"]
    ids = [t["id"] for t in tasks]
    assert ids == ["recent-cancel", "mid-done", "old-done"]
    statuses = {t["id"]: t["status"] for t in tasks}
    assert statuses["recent-cancel"] == "cancelled"
    assert statuses["mid-done"] == "done"
    assert statuses["old-done"] == "done"
    # Notion-фильтр включает все три статуса
    f_str = _json.dumps(captured["filters"], ensure_ascii=False)
    assert "Done" in f_str and "Complete" in f_str and "Archived" in f_str


# ── Bug 2 ──────────────────────────────────────────────────────────────────

def _check_item(iid, name, group):
    return {
        "id": iid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "Тип": {"select": {"name": "📋 Чеклист"}},
            "Статус": {"status": {"name": "Not started"}},
            "Категория": {"select": None},
            "Количество": {"number": None},
            "Цена": {"number": None},
            "Заметка": {"rich_text": []},
            "Срок годности": {"date": None},
            "Повторяющийся": {"checkbox": False},
            "Группа": {"rich_text": [{"plain_text": group}]},
        },
    }


def _parent_task(title, *, status="Not started"):
    return {
        "id": f"parent-{title}",
        "properties": {
            "Задача": {"title": [{"plain_text": title}]},
            "Статус": {"status": {"name": status}},
            "Приоритет": {"select": {"name": "🔴 Срочно"}},
            "Категория": {"select": {"name": "🏠 Дом"}},
            "Дедлайн": {"date": None},
            "Напоминание": {"date": None},
            "Время повтора": {"rich_text": []},
            "Повтор": {"select": None},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
        },
    }


def test_check_items_with_done_parent_are_hidden(client):
    """Чеклист задачи в статусе Done не должен возвращаться endpoint'ом."""
    DB_LISTS = "db-lists-id"
    DB_TASKS = "db-tasks-id"

    list_pages = [
        _check_item("c1", "помыть холодильник", group="Генеральная уборка"),
        _check_item("c2", "купить молоко", group="Покупки на неделю"),
        _check_item("c3", "выкинуть просрочку", group="Архив прошлый год"),
    ]
    task_pages = [
        _parent_task("Генеральная уборка", status="Done"),
        _parent_task("Покупки на неделю", status="Not started"),
        _parent_task("Архив прошлый год", status="Archived"),
    ]

    async def qp(db_id, *, filters=None, **__):
        if db_id == DB_LISTS:
            return list_pages
        if db_id == DB_TASKS:
            return task_pages
        return []

    with patch("miniapp.backend.routes.lists.query_pages", side_effect=qp), \
         patch("miniapp.backend.routes.lists.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("miniapp.backend.routes.lists.today_user_tz",
               AsyncMock(return_value=(_today_date(), 3))), \
         patch("miniapp.backend.routes.lists.config") as cfg:
        cfg.db_lists = DB_LISTS
        cfg.nexus.db_tasks = DB_TASKS
        r = client.get("/api/lists?type=check")

    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [i["id"] for i in items]
    # c1 (parent Done) и c3 (parent Archived) спрятаны, c2 (parent Not started) виден
    assert ids == ["c2"]
