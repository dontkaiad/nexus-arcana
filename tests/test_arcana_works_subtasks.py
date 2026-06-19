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


def _pg_work(wid, title):
    from arcana.repos.works_repo import Work
    return Work(
        id=str(wid), title=title, priority="Можно потом",
        deadline_str="", category_str="", has_client=False,
        status="open", client_id=None, deadline_dt=None,
        reminder_dt=None, deadline_iso="", category="",
    )


def _mock_inv_repo(items=None):
    m = MagicMock()
    m.get_items_for_works = AsyncMock(return_value=items or [])
    return m


def test_arcana_works_payload_contains_subtasks(client):
    """После миграции Списки на PG: subtasks берутся из arcana_inventory."""
    pg_works = [_pg_work("w1", "Подготовить колоду"), _pg_work("w2", "Закупить свечи")]
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=pg_works)

    today = date(2026, 5, 3)
    with patch("miniapp.backend.routes.arcana_today._pg_works_repo", mock_repo), \
         patch("miniapp.backend.routes.arcana_today._arcana_inv_repo_lists",
               _mock_inv_repo([])), \
         patch("miniapp.backend.routes.arcana_today.load_clients_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.get("/api/arcana/works")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    by_id = {w["id"]: w for w in data["works"]}
    assert "subtasks" in by_id["w1"]
    assert by_id["w1"]["subtasks"] == []
    assert by_id["w2"]["subtasks"] == []


def test_arcana_works_subtasks_populated(client):
    """Когда arcana_inventory.get_items_for_works возвращает items — они видны в ответе."""
    from core.repos.pg_nexus_lists_repo import InventoryItem
    pg_works = [_pg_work("42", "Расклад")]
    sub = InventoryItem(
        id="99", name="Зажечь свечу", list_type="чеклист",
        status="not_started", works_id="42",
    )
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=pg_works)

    today = date(2026, 5, 3)
    with patch("miniapp.backend.routes.arcana_today._pg_works_repo", mock_repo), \
         patch("miniapp.backend.routes.arcana_today._arcana_inv_repo_lists",
               _mock_inv_repo([sub])), \
         patch("miniapp.backend.routes.arcana_today.load_clients_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.get("/api/arcana/works")

    assert r.status_code == 200, r.text
    data = r.json()
    work = data["works"][0]
    assert work["id"] == "42"
    assert len(work["subtasks"]) == 1
    assert work["subtasks"][0]["id"] == "99"
    assert work["subtasks"][0]["name"] == "Зажечь свечу"
    assert work["subtasks"][0]["done"] is False


def test_arcana_works_returns_open_only(client):
    """done/archived works фильтруются, open — остаются."""
    from arcana.repos.works_repo import Work
    all_works = [
        _pg_work("w1", "Открытая работа"),
        Work(id="w2", title="Готово", priority="Важно", deadline_str="",
             category_str="", has_client=False, status="done"),
        Work(id="w3", title="Отменена", priority="Можно потом", deadline_str="",
             category_str="", has_client=False, status="archived"),
    ]
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=all_works)

    today = date(2026, 5, 3)
    with patch("miniapp.backend.routes.arcana_today._pg_works_repo", mock_repo), \
         patch("miniapp.backend.routes.arcana_today._arcana_inv_repo_lists",
               _mock_inv_repo([])), \
         patch("miniapp.backend.routes.arcana_today.load_clients_map",
               AsyncMock(return_value={})), \
         patch("miniapp.backend.routes.arcana_today.today_user_tz",
               AsyncMock(return_value=(today, 3))), \
         patch("miniapp.backend.routes.arcana_today.get_user_notion_id",
               AsyncMock(return_value=FAKE_NOTION)):
        r = client.get("/api/arcana/works")

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["works"][0]["id"] == "w1"


@pytest.mark.asyncio
async def test_subtasks_handler_full_uuid_no_scan():
    """Кнопка с полным UUID → pending ставится с точным task_id, db_query не вызывается."""
    from core.subtasks_handler import task_subtask_cb

    FULL_UUID = "deadbeef-1234-5678-9abc-def012345678"

    call = MagicMock()
    call.from_user.id = 7
    call.data = f"task_subtask_work_{FULL_UUID}"
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
         patch("core.user_manager.get_user_notion_id", AsyncMock(return_value="notion-u-42")):
        await task_subtask_cb(call)

    assert captured.get("uid") == 7
    assert captured["data"]["action"] == "subtask_items"
    assert captured["data"]["rel_type"] == "work"
    assert captured["data"]["task_id"] == FULL_UUID, (
        "task_id должен быть полным UUID, а не усечённым"
    )
    assert captured["data"]["task_name"] == "Подготовить колоду"
    assert captured["data"]["bot"] == "arcana"
    assert captured["data"]["user_notion_id"] == "notion-u-42", (
        "user_notion_id должен быть реальным, не пустой строкой"
    )
    call.message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_subtasks_handler_truncated_id_not_found_fails_gracefully():
    """Регрессия #109: усечённый id_prefix не найден в db → ошибка юзеру, сирота НЕ создаётся.

    Старый код: task_id = id_prefix (усечённый) → pending_set вызывался с невалидным UUID.
    Новый код: task_id = None → сообщение об ошибке → return (pending_set НЕ вызывается).
    """
    from core.subtasks_handler import task_subtask_cb

    call = MagicMock()
    call.from_user.id = 7
    call.data = "task_subtask_work_abc123def456"  # усечённый, не full UUID
    call.message = MagicMock()
    call.message.text = "⚡ Работа создана!\n📌 Колода\nfoo"
    call.message.edit_reply_markup = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()

    captured = {}

    def fake_set(uid, data):
        captured["uid"] = uid
        captured["data"] = data

    # db_query возвращает пустой список — задача не найдена
    with patch("core.list_manager.pending_set", fake_set), \
         patch("core.user_manager.get_user_notion_id", AsyncMock(return_value="u")):
        await task_subtask_cb(call)

    # pending_set НЕ должен был вызваться — сирота не создаётся
    assert "data" not in captured, (
        "pending_set не должен вызываться при неразрешённом task_id"
    )
    # Пользователь получает сообщение об ошибке
    call.message.answer.assert_awaited_once()
    error_text = call.message.answer.call_args.args[0]
    assert "не удалось" in error_text.lower() or "⚠️" in error_text


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

    with patch.object(wp._works_repo, "create",
                      AsyncMock(return_value="123456789")), \
         patch("arcana.handlers.work_preview.get_user_tz",
               AsyncMock(return_value=3)), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await wp.cb_work_save(call)

    edit_kwargs = call.message.edit_text.call_args.kwargs
    kb = edit_kwargs.get("reply_markup")
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = [b.callback_data for b in flat]
    subtask_cb = next((c for c in cbs if c.startswith("task_subtask_work_")), None)
    assert subtask_cb is not None
    # После фикса #109: callback_data содержит полный page_id, а не усечённый prefix
    page_id_part = subtask_cb.removeprefix("task_subtask_work_")
    assert page_id_part == "123456789", (
        f"Ожидали полный page_id в callback_data, получили: {page_id_part!r}"
    )
    assert "work_ok" in cbs


# ── Regression #110: user_notion_id stored in pending ────────────────────────


@pytest.mark.asyncio
async def test_subtasks_handler_stores_real_user_notion_id():
    """Регрессия #110: pending должен содержать реальный user_notion_id, а не ''.

    Старый код: 'user_notion_id': '' → pending.get('user_notion_id', fallback) = ''
    (ключ существует со значением '') → 🪪 Пользователи не ставилась на чеклист-пункты.
    Новый код: get_user_notion_id(tg_id) → реальный id → pending.get(...) or fallback = реальный id.
    """
    from core.subtasks_handler import task_subtask_cb

    FULL_UUID = "cafebabe-1234-5678-9abc-def012345678"

    call = MagicMock()
    call.from_user.id = 42
    call.data = f"task_subtask_task_{FULL_UUID}"
    call.message = MagicMock()
    call.message.text = "⚡ Задача создана!\n📌 Отладить код\nfoo"
    call.message.edit_reply_markup = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()

    captured = {}

    def fake_set(uid, data):
        captured["data"] = data

    with patch("core.list_manager.pending_set", fake_set), \
         patch("core.user_manager.get_user_notion_id",
               AsyncMock(return_value="real-notion-page-id")):
        await task_subtask_cb(call)

    assert "data" in captured, "pending_set должен был вызваться"
    stored_uid = captured["data"]["user_notion_id"]
    assert stored_uid == "real-notion-page-id", (
        f"Ожидали реальный user_notion_id, получили: {stored_uid!r} "
        "(пустая строка = баг #110)"
    )
