"""Тесты core/bot_notify + что write-роуты Mini App дёргают notify_user.

Уведа в бота при действиях из мини-аппы (issue #72). notify_user всюду
заглушён autouse-фикстурой `_mute_bot_notify`; здесь местами снимаем её,
чтобы проверить, что роут реально вызвал notify_user.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from nexus.repos.pg_tasks_repo import Task as PgTask

FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _task_page(pid: str, title: str = "разобрать гардероб") -> dict:
    return {
        "id": pid,
        "properties": {
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
            "Статус": {"status": {"name": "Not started"}},
            "Задача": {"title": [{"plain_text": title}]},
        },
    }


# ── notify_user (unit) ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_user_posts_to_telegram():
    from core import bot_notify
    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    with patch.object(bot_notify.httpx, "AsyncClient", _Client):
        ok = await bot_notify.notify_user(123, "<b>привет</b>", bot="nexus")
    assert ok is True
    assert "/sendMessage" in captured["url"]
    assert captured["json"]["chat_id"] == 123
    assert captured["json"]["text"] == "<b>привет</b>"
    assert captured["json"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_notify_user_swallows_errors():
    from core import bot_notify

    class _Boom:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            raise RuntimeError("network down")
        async def __aexit__(self, *a):
            return False

    with patch.object(bot_notify.httpx, "AsyncClient", _Boom):
        ok = await bot_notify.notify_user(123, "x", bot="nexus")
    assert ok is False


@pytest.mark.asyncio
async def test_notify_user_no_token_for_arcana():
    from core import bot_notify
    with patch.object(bot_notify.config.arcana, "tg_token", ""):
        ok = await bot_notify.notify_user(123, "x", bot="arcana")
    assert ok is False


# ── routes call notify_user ──────────────────────────────────────────────────

def test_task_done_notifies(client):
    task = PgTask(id="task-1", title="разобрать гардероб", user_notion_id=FAKE_NOTION_USER)
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes.notify_user", notify), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_props",
               AsyncMock(return_value=None)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)), \
         patch("nexus.handlers.streaks.update_streak", AsyncMock(return_value=None)):
        r = client.post("/api/tasks/task-1/done")
    assert r.status_code == 200
    notify.assert_awaited_once()
    args, kwargs = notify.call_args
    assert args[0] == FAKE_TG_ID
    assert "разобрать гардероб" in args[1]
    assert kwargs.get("bot") == "nexus"


def test_task_create_notifies(client):
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes.notify_user", notify), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.create",
               AsyncMock(return_value="new-id")), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks", json={"title": "купить молоко"})
    assert r.status_code == 200
    notify.assert_awaited_once()
    assert "купить молоко" in notify.call_args[0][1]
    assert notify.call_args.kwargs.get("bot") == "nexus"


def test_task_cancel_notifies(client):
    task = PgTask(id="task-9", title="старая задача", user_notion_id=FAKE_NOTION_USER)
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes.notify_user", notify), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
               AsyncMock(return_value=task)), \
         patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
               AsyncMock(return_value=True)), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/tasks/task-9/cancel")
    assert r.status_code == 200
    notify.assert_awaited_once()
    assert notify.call_args.kwargs.get("bot") == "nexus"


def test_session_verify_notifies(client):
    # #7: вердикт расклада из детали тоже шлёт уведу (как accuracy/verify).
    from decimal import Decimal
    from arcana.repos.sessions_repo import TripletEntry
    triplet = TripletEntry(
        id="s-1", question="деньги в марте", cards="", interpretation="",
        deck="Уэйт", session_name="", client_id=None,
        date="2026-05-01", outcome="unverified",
        amount=Decimal("0"), paid=Decimal("0"),
        spread_type="", area="", barter_what="", bottom_card="", photo_url=None,
    )
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=triplet)
    mock_repo.set_outcome = AsyncMock(return_value=True)
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes.notify_user", notify), \
         patch("miniapp.backend.routes.writes._sessions_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/sessions/s-1/verify", json={"status": "✅ Да"})
    assert r.status_code == 200
    notify.assert_awaited_once()
    assert notify.call_args.kwargs.get("bot") == "arcana"
    assert "деньги в марте" in notify.call_args[0][1]
    assert "сбылось" in notify.call_args[0][1]


def test_ritual_result_notifies(client):
    # #8: результат ритуала из детали тоже шлёт уведу (как session_verify).
    from arcana.repos.rituals_repo import Ritual
    ritual = Ritual(id="r-1", name="ритуал на защиту")
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=ritual)
    mock_repo.set_result = AsyncMock(return_value=True)
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes.notify_user", notify), \
         patch("miniapp.backend.routes.writes._rituals_pg_repo", mock_repo), \
         patch("miniapp.backend.routes.writes.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/rituals/r-1/result", json={"status": "✅ Сработало"})
    assert r.status_code == 200
    notify.assert_awaited_once()
    assert notify.call_args.kwargs.get("bot") == "arcana"
    assert "ритуал на защиту" in notify.call_args[0][1]
    assert "сработало" in notify.call_args[0][1]


def test_arcana_work_done_notifies(client):
    # #10: отметка Работы done из Mini App шлёт уведу в Arcana-бот.
    from arcana.repos.works_repo import Work
    pg_work = Work(
        id="42", title="расклад на неделю", priority="Важно",
        deadline_str="", category_str="", has_client=False, status="open",
    )
    mock_repo = MagicMock()
    mock_repo.find_by_id = AsyncMock(return_value=pg_work)
    mock_repo.set_status = AsyncMock(return_value=True)
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.writes.notify_user", notify), \
         patch("miniapp.backend.routes.writes._works_pg_repo", mock_repo):
        r = client.post("/api/arcana/works/42/done")
    assert r.status_code == 200
    notify.assert_awaited_once()
    assert notify.call_args.kwargs.get("bot") == "arcana"
    assert "расклад на неделю" in notify.call_args[0][1]


def test_arcana_accuracy_verify_notifies(client):
    mock_sess_repo = MagicMock()
    mock_sess_repo.set_outcome = AsyncMock(return_value=True)
    mock_sess_repo.list_all = AsyncMock(return_value=[])
    notify = AsyncMock(return_value=True)
    with patch("miniapp.backend.routes.arcana_today.notify_user", notify), \
         patch("miniapp.backend.routes.arcana_today._pg_sessions_repo", mock_sess_repo), \
         patch("miniapp.backend.routes.arcana_today.rituals_all",
               AsyncMock(return_value=[])), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION_USER)):
        r = client.post("/api/arcana/accuracy/verify",
                        json={"id": "s-1", "type": "session", "verdict": "yes"})
    assert r.status_code == 200
    notify.assert_awaited_once()
    assert notify.call_args.kwargs.get("bot") == "arcana"
    assert "Расклад" in notify.call_args[0][1]
