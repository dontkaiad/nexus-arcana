"""tests/test_arcana_works_subtasks.py — /api/arcana/works отдаёт подзадачи
одним batch-запросом + Arcana-кнопка работает через общий core router.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id


FAKE_TG = 67686090
FAKE_NOTION = "user-notion-id-42"


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _work_page(wid: str, title: str, status: str = "Not started") -> dict:
    return {
        "id": wid,
        "properties": {
            "Работа": {"title": [{"plain_text": title}]},
            "Status": {"status": {"name": status}},
            "Категория": {"select": None},
            "Приоритет": {"select": None},
            "Дедлайн": {"date": None},
            "👥 Клиенты": {"relation": []},
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION}]},
        },
    }


def _subtask_page(sid: str, name: str, work_id: str, done: bool = False) -> dict:
    return {
        "id": sid,
        "properties": {
            "Название": {"title": [{"plain_text": name}]},
            "Тип": {"select": {"name": "📋 Чеклист"}},
            "Статус": {"status": {"name": "Done" if done else "Not started"}},
            "🔮 Работы": {"relation": [{"id": work_id}]},
        },
    }


def test_arcana_works_payload_contains_subtasks(client):
    works = [_work_page("w1", "Подготовить колоду"),
             _work_page("w2", "Закупить свечи")]
    subtasks = [
        _subtask_page("s1a", "Достать колоду", "w1"),
        _subtask_page("s1b", "Очистить", "w1", done=True),
        _subtask_page("s2", "Найти магазин", "w2"),
    ]

    async def fake_query(db_id, **kwargs):
        # Первый вызов — works, второй — lists. Различаем по фильтрам.
        f = (kwargs.get("filters") or {})
        # works filter имеет Status conditions; lists filter имеет Тип condition
        cond_str = str(f)
        if "Тип" in cond_str and "Чеклист" in cond_str:
            return subtasks
        return works

    today = date(2026, 5, 3)
    with patch("miniapp.backend.routes.arcana_today.query_pages",
               AsyncMock(side_effect=fake_query)), \
         patch("core.notion_client.query_pages",
               AsyncMock(side_effect=fake_query)), \
         patch("miniapp.backend.routes.arcana_today._client_types_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.load_clients_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_today._works_schedule",
               AsyncMock(return_value=([], []))):
        r = client.get("/api/arcana/works")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    by_id = {w["id"]: w for w in data["works"]}
    assert "subtasks" in by_id["w1"]
    s1 = by_id["w1"]["subtasks"]
    assert len(s1) == 2
    assert {x["name"] for x in s1} == {"Достать колоду", "Очистить"}
    assert {x["done"] for x in s1} == {True, False}
    s2 = by_id["w2"]["subtasks"]
    assert len(s2) == 1 and s2[0]["name"] == "Найти магазин"


def test_arcana_works_subtasks_batched_in_one_query(client):
    """Гарант перформанса: одну работу или сто — query_pages для Списков
    вызывается ровно один раз."""
    works = [_work_page(f"w{i}", f"R{i}") for i in range(5)]

    call_count = {"lists": 0}

    async def fake_query(db_id, **kwargs):
        cond_str = str(kwargs.get("filters") or {})
        if "Чеклист" in cond_str:
            call_count["lists"] += 1
            return []
        return works

    today = date(2026, 5, 3)
    with patch("miniapp.backend.routes.arcana_today.query_pages",
               AsyncMock(side_effect=fake_query)), \
         patch("core.notion_client.query_pages",
               AsyncMock(side_effect=fake_query)), \
         patch("miniapp.backend.routes.arcana_today._client_types_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.load_clients_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)), \
         patch("miniapp.backend.routes.arcana_today._works_schedule",
               AsyncMock(return_value=([], []))):
        r = client.get("/api/arcana/works")

    assert r.status_code == 200
    assert call_count["lists"] == 1, "subtasks must be fetched in ONE batch query"


@pytest.mark.asyncio
async def test_subtasks_handler_callback_works_for_arcana_rel():
    """Общий core/subtasks_handler принимает rel_type=work и резолвит db_works."""
    from core.subtasks_handler import task_subtask_cb  # noqa

    call = MagicMock()
    call.from_user.id = 7
    call.data = "task_subtask_work_abc123"
    call.message = MagicMock()
    call.message.text = "⚡ Работа создана!\n📌 Подготовить колоду\nfoo"
    call.message.edit_reply_markup = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()

    captured = {}

    def fake_set(uid, data):
        captured["uid"] = uid
        captured["data"] = data

    with patch("core.list_manager.pending_set", fake_set), \
         patch("core.notion_client.db_query", AsyncMock(return_value=[])):
        await task_subtask_cb(call)

    assert captured["uid"] == 7
    assert captured["data"]["action"] == "subtask_items"
    assert captured["data"]["rel_type"] == "work"
    assert captured["data"]["task_name"] == "Подготовить колоду"
    assert captured["data"]["bot"] == "arcana"
    call.message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_work_save_attaches_subtasks_button():
    """После cb_work_save финальное сообщение содержит кнопку 📋 Подзадачи
    с правильным callback_data."""
    import arcana.handlers.work_preview as wp
    import os, tempfile
    tmp = tempfile.NamedTemporaryFile(suffix="_pw_subtasks_btn.db", delete=False).name
    if os.path.exists(tmp):
        os.remove(tmp)
    wp._PENDING_DB = tmp

    slug = wp._make_slug(7)
    wp._pending_set(7, slug, {
        "title": "Колода", "category": "✨ Ритуал",
        "priority": "Важно", "work_type": "🌟 Личная",
        "client_name": None, "client_id": None,
        "deadline": None, "reminder": None,
        "msg_id": 1, "chat_id": 100, "user_notion_id": "u",
    })

    call = MagicMock()
    call.from_user.id = 7
    call.data = f"work_save:{slug}"
    call.message = MagicMock()
    call.message.chat.id = 100
    call.message.message_id = 1
    call.message.edit_text = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()

    with patch("arcana.handlers.work_preview.work_add",
               AsyncMock(return_value="page-id-deadbeef-1234-5678-9abc-def012")), \
         patch("arcana.handlers.work_preview.get_user_tz",
               AsyncMock(return_value=3)), \
         patch("core.notion_client.update_page", AsyncMock()), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await wp.cb_work_save(call)

    edit_kwargs = call.message.edit_text.call_args.kwargs
    kb = edit_kwargs.get("reply_markup")
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    assert any(c.startswith("task_subtask_work_") for c in cbs)
    assert "work_ok" in cbs
