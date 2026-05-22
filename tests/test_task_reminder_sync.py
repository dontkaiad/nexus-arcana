"""#73: отметка задачи в Mini App гасит живую плашку-напоминание в чате.

Покрываем стор core.task_reminder_msg + core.bot_notify.clear_task_reminder
+ что Mini App task_done зовёт clear_task_reminder.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


@pytest.fixture
def _tmp_store(tmp_path, monkeypatch):
    import core.task_reminder_msg as trm
    monkeypatch.setattr(trm, "DB_PATH", str(tmp_path / "trm.db"))
    return trm


# ── store ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_save_get_delete(_tmp_store):
    trm = _tmp_store
    await trm.save_task_reminder("task-1", 111, 222, "менять лоток")
    row = await trm.get_task_reminder("task-1")
    assert row == {
        "chat_id": 111, "message_id": 222, "title": "менять лоток",
        "created_at": row["created_at"],
    }
    await trm.delete_task_reminder("task-1")
    assert await trm.get_task_reminder("task-1") is None


@pytest.mark.asyncio
async def test_store_overwrites_latest(_tmp_store):
    trm = _tmp_store
    await trm.save_task_reminder("task-1", 111, 1, "t")
    await trm.save_task_reminder("task-1", 111, 2, "t")
    row = await trm.get_task_reminder("task-1")
    assert row["message_id"] == 2  # одна живая плашка на задачу


@pytest.mark.asyncio
async def test_store_ttl_expired(_tmp_store):
    trm = _tmp_store
    await trm.save_task_reminder("old", 111, 5, "t")
    # подменяем created_at на 48ч назад
    import aiosqlite
    async with aiosqlite.connect(trm.DB_PATH) as db:
        await db.execute("UPDATE task_reminder_msg SET created_at=? WHERE task_id=?",
                         (time.time() - 48 * 3600, "old"))
        await db.commit()
    assert await trm.get_task_reminder("old") is None


# ── clear_task_reminder ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_edits_message_and_deletes_row(_tmp_store):
    trm = _tmp_store
    await trm.save_task_reminder("task-1", 111, 222, "менять лоток котам")

    from core import bot_notify
    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    with patch.object(bot_notify.httpx, "AsyncClient", _Client):
        ok = await bot_notify.clear_task_reminder("task-1", bot="nexus")

    assert ok is True
    assert "/editMessageText" in captured["url"]
    assert captured["json"]["chat_id"] == 111
    assert captured["json"]["message_id"] == 222
    assert "менять лоток котам" in captured["json"]["text"]
    assert "отмечено в приложении" in captured["json"]["text"]
    # reply_markup не передаём → Telegram снимает кнопки
    assert "reply_markup" not in captured["json"]
    # строка удалена
    assert await trm.get_task_reminder("task-1") is None


@pytest.mark.asyncio
async def test_clear_noop_when_no_row(_tmp_store):
    from core import bot_notify
    ok = await bot_notify.clear_task_reminder("ghost", bot="nexus")
    assert ok is False


@pytest.mark.asyncio
async def test_clear_swallows_edit_error_and_still_deletes(_tmp_store):
    trm = _tmp_store
    await trm.save_task_reminder("task-1", 111, 222, "t")
    from core import bot_notify

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("down")
        async def __aexit__(self, *a): return False

    with patch.object(bot_notify.httpx, "AsyncClient", _Boom):
        ok = await bot_notify.clear_task_reminder("task-1", bot="nexus")
    assert ok is False
    assert await trm.get_task_reminder("task-1") is None  # строку всё равно убрали


# ── Mini App task_done зовёт clear ──────────────────────────────────────────

def test_task_done_calls_clear_task_reminder():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    page = {
        "id": "task-1",
        "properties": {
            "🪪 Пользователи": {"relation": [{"id": FAKE_NOTION_USER}]},
            "Статус": {"status": {"name": "Not started"}},
            "Задача": {"title": [{"plain_text": "разобрать гардероб"}]},
        },
    }
    clear = AsyncMock(return_value=True)
    try:
        with patch("miniapp.backend.routes.writes.clear_task_reminder", clear), \
             patch("miniapp.backend.routes.writes.get_page", AsyncMock(return_value=page)), \
             patch("miniapp.backend.routes.writes.update_task_status", AsyncMock(return_value=True)), \
             patch("miniapp.backend.routes.writes.update_page", AsyncMock(return_value=None)), \
             patch("miniapp.backend.routes.writes.get_user_notion_id",
                   AsyncMock(return_value=FAKE_NOTION_USER)), \
             patch("nexus.handlers.streaks.update_streak", AsyncMock(return_value=None)):
            c = TestClient(app)
            r = c.post("/api/tasks/task-1/done")
        assert r.status_code == 200
        clear.assert_awaited_once_with("task-1", bot="nexus")
    finally:
        app.dependency_overrides.clear()
