"""tests/test_work_preview.py — preview-flow Работы (паритет с Nexus tasks).

Покрывает:
1. handle_add_work НЕ создаёт запись в Notion сразу.
2. pending_state создан с распарсенными полями.
3. Уточнение «через 2 дня» обновляет pending.deadline + перерисовывает превью.
4. work_save callback пишет в Notion + ставит reminder + удаляет pending.
5. work_cancel callback удаляет pending без записи.
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Подменяем pending DB на временный файл, чтобы тесты не плодили pending_works.db в репе
_TMP_DB = tempfile.NamedTemporaryFile(suffix="_pending_works.db", delete=False).name
import arcana.handlers.work_preview as wp  # noqa: E402
wp._PENDING_DB = _TMP_DB


def _fresh_db():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


def _make_message(uid: int = 42, text: str = "сделать ритуал для маши") -> MagicMock:
    msg = MagicMock()
    msg.from_user.id = uid
    msg.chat.id = 100
    msg.text = text
    msg.bot = MagicMock()
    msg.bot.edit_message_text = AsyncMock()
    answered = MagicMock()
    answered.message_id = 999
    answered.chat.id = 100
    msg.answer = AsyncMock(return_value=answered)
    return msg


@pytest.mark.asyncio
async def test_handle_add_work_does_not_write_to_notion():
    _fresh_db()
    msg = _make_message()
    parsed = {
        "title": "Ритуал для Маши",
        "category": "✨ Ритуал",
        "priority": "Важно",
        "work_type": "🤝 Клиентская",
        "client_name": "Маша",
        "deadline": None,
        "reminder": None,
    }

    import core.client_resolve as cr
    with patch.object(wp, "_parse_work_text", AsyncMock(return_value=parsed)), \
         patch.object(cr, "resolve_or_create",
                      AsyncMock(return_value="client-1")), \
         patch.object(wp, "get_user_tz", AsyncMock(return_value=3)), \
         patch("arcana.handlers.work_preview.work_add", AsyncMock()) as add_mock:
        await wp.handle_add_work_preview(msg, "ритуал для Маши", "user-notion")

    add_mock.assert_not_called()
    # pending создан
    pending = wp._pending_get(42)
    assert pending is not None
    assert pending["title"] == "Ритуал для Маши"
    assert pending["client_id"] == "client-1"
    assert pending["deadline"] is None
    assert "_slug" in pending


@pytest.mark.asyncio
async def test_clarification_updates_deadline_and_edits_preview():
    _fresh_db()
    # начальное состояние: pending без дедлайна
    slug = wp._make_slug(42)
    wp._pending_set(42, slug, {
        "title": "Ритуал", "category": "✨ Ритуал", "priority": "Важно",
        "work_type": "🌟 Личная", "client_name": None, "client_id": None,
        "deadline": None, "reminder": None,
        "msg_id": 999, "chat_id": 100, "user_notion_id": "u",
    })

    msg = _make_message(uid=42, text="через 2 дня")
    with patch.object(wp, "_parse_clarification",
                      AsyncMock(return_value={"deadline": "2026-05-05"})), \
         patch.object(wp, "get_user_tz", AsyncMock(return_value=3)), \
         patch("core.utils.react", AsyncMock()):
        handled = await wp.handle_work_clarification(msg)

    assert handled is True
    pending = wp._pending_get(42)
    assert pending["deadline"] == "2026-05-05"
    # Превью отредактировано (edit_message_text вызван)
    msg.bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_work_save_creates_notion_and_schedules_reminder():
    _fresh_db()
    slug = wp._make_slug(42)
    wp._pending_set(42, slug, {
        "title": "Ритуал", "category": "✨ Ритуал", "priority": "Важно",
        "work_type": "🤝 Клиентская", "client_name": "Маша", "client_id": "c1",
        "deadline": "2026-05-05", "reminder": None,
        "msg_id": 999, "chat_id": 100, "user_notion_id": "u",
    })

    call = MagicMock()
    call.from_user.id = 42
    call.data = f"work_save:{slug}"
    call.message = MagicMock()
    call.message.chat.id = 100
    call.message.message_id = 999
    call.message.edit_text = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()

    flow = MagicMock()
    flow.schedule_reminder = AsyncMock(return_value=True)
    fake_bot = MagicMock(arcana_reminder_flow=flow)

    with patch("arcana.handlers.work_preview.work_add",
               AsyncMock(return_value="page-id-xyz")) as add_mock, \
         patch.dict("sys.modules", {"arcana.bot": fake_bot}), \
         patch("arcana.handlers.work_preview.get_user_tz",
               AsyncMock(return_value=3)), \
         patch("core.notion_client.update_page", AsyncMock()), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await wp.cb_work_save(call)

    add_mock.assert_awaited_once()
    flow.schedule_reminder.assert_awaited_once()
    # reminder = deadline - 1 день
    kwargs = flow.schedule_reminder.call_args.kwargs
    assert kwargs["reminder_dt"].startswith("2026-05-04")
    # pending удалён
    assert wp._pending_get(42) is None


@pytest.mark.asyncio
async def test_work_cancel_deletes_pending_without_notion_write():
    _fresh_db()
    slug = wp._make_slug(42)
    wp._pending_set(42, slug, {
        "title": "Ритуал", "deadline": None, "reminder": None,
        "msg_id": 999, "chat_id": 100,
    })

    call = MagicMock()
    call.from_user.id = 42
    call.data = f"work_cancel:{slug}"
    call.message = MagicMock()
    call.message.edit_text = AsyncMock()
    call.answer = AsyncMock()

    with patch("arcana.handlers.work_preview.work_add",
               AsyncMock()) as add_mock, \
         patch("core.utils.react", AsyncMock()):
        await wp.cb_work_cancel(call)

    add_mock.assert_not_called()
    assert wp._pending_get(42) is None
