"""Wave 3 tests — POST endpoints (tasks/finance/lists/memory/arcana writes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _page(pid: str, *, owner: str = FAKE_NOTION_USER, extra: dict | None = None) -> dict:
    props = {
        "🪪 Пользователи": {"relation": [{"id": owner}]},
        "Статус": {"status": {"name": "Not started"}},
        "Задача": {"title": [{"plain_text": "Test"}]},
    }
    if extra:
        props.update(extra)
    return {"id": pid, "properties": props}


def _today_date(tz: int = 3):
    return (datetime.now(timezone.utc) + timedelta(hours=tz)).date()


# ─── /api/tasks/{id}/done ───────────────────────────────────────────────────

def test_task_done_updates_status(client):
    target = _page("task-1")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=target)), \
         patch("miniapp.backend.routes.writes.update_task_status",
               AsyncMock(return_value=True)) as upd, \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/task-1/done")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    upd.assert_awaited_once_with("task-1", "Done")


def test_task_done_rejects_stranger(client):
    foreign = _page("task-2", owner="not-my-user")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=foreign)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/task-2/done")
    assert r.status_code == 404


def test_task_done_404_when_page_missing(client):
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/missing-id/done")
    assert r.status_code == 404


# ─── /api/tasks/{id}/postpone ───────────────────────────────────────────────

def test_task_postpone_shifts_date(client):
    tz = 3
    today = _today_date(tz)
    page = _page("t-3", extra={"Дедлайн": {"date": {"start": today.isoformat()}}})
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_task_deadline",
               AsyncMock(return_value=True)) as upd, \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/t-3/postpone", json={"days": 2})
    assert r.status_code == 200
    expected = (today + timedelta(days=2)).isoformat()
    assert r.json()["new_date"] == expected
    upd.assert_awaited_once_with("t-3", expected)


def test_task_postpone_falls_back_to_today_when_no_deadline(client):
    tz = 3
    today = _today_date(tz)
    page = _page("t-4", extra={"Дедлайн": {"date": None}})
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_task_deadline",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value="")):
        r = client.post("/api/tasks/t-4/postpone", json={"days": 5})
    assert r.status_code == 200
    assert r.json()["new_date"] == (today + timedelta(days=5)).isoformat()


# ─── /api/tasks/{id}/cancel ─────────────────────────────────────────────────

def test_task_cancel_sets_archived(client):
    page = _page("t-5")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_task_status",
               AsyncMock(return_value=True)) as upd, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/t-5/cancel")
    assert r.status_code == 200
    upd.assert_awaited_once_with("t-5", "Archived")


# ─── /api/tasks (create) ────────────────────────────────────────────────────

def test_task_create_minimal(client):
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="new-id")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks", json={"title": "Купить молоко"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "new-id"}
    args, _ = pc.await_args
    _, props = args
    assert props["Задача"]["title"][0]["text"]["content"] == "Купить молоко"
    # Дефолтный статус
    assert props["Статус"]["status"]["name"] == "Not started"


# ─── /api/expenses ──────────────────────────────────────────────────────────

def test_expense_create_uses_finance_add(client):
    tz = 3
    today = _today_date(tz)
    with patch("miniapp.backend.routes.writes.finance_add",
               AsyncMock(return_value="fin-id")) as fa, \
         patch("miniapp.backend.routes.writes.today_user_tz",
               AsyncMock(return_value=(today, tz))), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/expenses", json={
            "amount": 1500,
            "cat": "🚬 Привычки",
            "desc": "Chapman",
            "bot": "nexus",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["id"] == "fin-id"
    kwargs = fa.await_args.kwargs
    assert kwargs["amount"] == 1500
    assert kwargs["category"] == "🚬 Привычки"
    assert kwargs["type_"] == "💸 Расход"
    assert kwargs["bot_label"] == "☀️ Nexus"
    assert kwargs["date"] == today.isoformat()


def test_expense_rejects_zero_amount(client):
    r = client.post("/api/expenses", json={"amount": 0, "cat": "🍜 Продукты"})
    assert r.status_code == 422  # pydantic validation


# ─── /api/arcana/sessions/{id}/verify ───────────────────────────────────────

def test_session_verify_updates_select(client):
    page = _page("s-1")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)) as ups, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "✅ Да"})
    assert r.status_code == 200
    ups.assert_awaited_once_with("s-1", "Сбылось", "✅ Да")


def test_session_verify_rejects_unknown_status(client):
    with patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "😀 bogus"})
    assert r.status_code == 400


# ─── /api/arcana/rituals/{id}/result ────────────────────────────────────────

def test_ritual_result_updates_select(client):
    page = _page("r-1")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)) as ups, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/rituals/r-1/result",
                        json={"status": "✅ Сработало"})
    assert r.status_code == 200
    ups.assert_awaited_once_with("r-1", "Результат", "✅ Сработало")


# ─── /api/arcana/clients ────────────────────────────────────────────────────

def test_arcana_client_create(client):
    with patch("miniapp.backend.routes.writes.client_add",
               AsyncMock(return_value="cli-id")) as ca, \
         patch("miniapp.backend.routes.writes.update_page_select",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/clients", json={
            "name": "Анна",
            "contact": "@anna_tarot",
            "request": "Отношения",
            "status": "🟢 Активный",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "cli-id"}
    kwargs = ca.await_args.kwargs
    assert kwargs["name"] == "Анна"
    assert kwargs["contact"] == "@anna_tarot"


# ─── /api/lists create/done/delete ──────────────────────────────────────────

def test_list_create_buy(client):
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="list-id")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists", json={
            "type": "buy",
            "name": "Молоко",
            "cat": "🍜 Продукты",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "list-id"}
    args, _ = pc.await_args
    _, props = args
    assert props["Тип"]["select"]["name"] == "🛒 Покупки"
    assert props["Название"]["title"][0]["text"]["content"] == "Молоко"


def test_list_create_invalid_type(client):
    r = client.post("/api/lists", json={"type": "bogus", "name": "x"})
    assert r.status_code == 400


def test_list_done(client):
    page = _page("l-1")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists/l-1/done")
    assert r.status_code == 200
    up.assert_awaited_once()
    args, _ = up.await_args
    assert args[0] == "l-1"
    assert args[1]["Статус"]["status"]["name"] == "Done"


def test_list_delete_archives(client):
    page = _page("l-2")
    with patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
         patch("miniapp.backend.routes.writes.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/lists/l-2/delete")
    assert r.status_code == 200
    args, _ = up.await_args
    assert args[1]["Статус"]["status"]["name"] == "Archived"


# ─── /api/memory ────────────────────────────────────────────────────────────

def test_memory_create(client):
    with patch("miniapp.backend.routes.writes.page_create",
               AsyncMock(return_value="mem-id")) as pc, \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/memory", json={
            "text": "Chapman = сигареты",
            "cat": "🛒 Предпочтения",
        })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "mem-id"}
    args, _ = pc.await_args
    _, props = args
    assert props["Текст"]["title"][0]["text"]["content"] == "Chapman = сигареты"
    assert props["Актуально"]["checkbox"] is True


# ─── 401 for all POST endpoints ─────────────────────────────────────────────

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
