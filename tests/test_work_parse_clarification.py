"""tests/test_work_parse_clarification.py — graceful clarification вместо
parse_error на коротком/неоднозначном вводе.
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import arcana.handlers.work_preview as wp

_TMP_DB = tempfile.NamedTemporaryFile(
    suffix="_pending_works_partial.db", delete=False
).name
wp._PENDING_DB = _TMP_DB


def _fresh():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


def _msg(uid: int, text: str) -> MagicMock:
    m = MagicMock()
    m.from_user.id = uid
    m.chat.id = 100
    m.text = text
    m.bot = MagicMock()
    m.bot.edit_message_text = AsyncMock()
    answered = MagicMock()
    answered.message_id = 999
    answered.chat.id = 100
    m.answer = AsyncMock(return_value=answered)
    return m


@pytest.mark.asyncio
async def test_short_input_does_not_log_to_errors_and_saves_partial():
    _fresh()
    msg = _msg(1, "сделать ещё одну работу")

    # Haiku возвращает дефолтный пустой JSON — нет деталей
    parsed = {
        "title": "Работа", "category": None, "priority": "Можно потом",
        "work_type": "🌟 Личная", "client_name": None, "deadline": None,
        "reminder": None,
    }
    log_mock = AsyncMock()
    with patch.object(wp, "_parse_work_text", AsyncMock(return_value=parsed)), \
         patch.object(wp, "get_user_tz", AsyncMock(return_value=3)), \
         patch.object(wp, "client_find", AsyncMock(return_value=None)), \
         patch.object(wp, "log_error", log_mock):
        await wp.handle_add_work_preview(msg, "сделать ещё одну работу", "u")

    log_mock.assert_not_called()
    msg.answer.assert_awaited_once()
    sent = msg.answer.await_args.args[0]
    assert "Что за работа" in sent or "Опиши" in sent

    pending = wp._pending_get(1)
    assert pending is not None
    assert pending.get("_partial") is True
    assert pending["fragment"] == "сделать ещё одну работу"


@pytest.mark.asyncio
async def test_json_decode_error_also_saves_partial_silently():
    _fresh()
    msg = _msg(1, "хз")

    boom = AsyncMock(side_effect=json.JSONDecodeError("x", "y", 0))
    log_mock = AsyncMock()
    with patch.object(wp, "_parse_work_text", boom), \
         patch.object(wp, "get_user_tz", AsyncMock(return_value=3)), \
         patch.object(wp, "log_error", log_mock):
        await wp.handle_add_work_preview(msg, "хз", "u")

    log_mock.assert_not_called()
    pending = wp._pending_get(1)
    assert pending is not None and pending.get("_partial") is True


@pytest.mark.asyncio
async def test_partial_pending_followup_text_merges_and_reparses():
    _fresh()
    # Сидим partial pending
    slug = wp._make_slug(1)
    wp._pending_set(1, slug, {
        "_partial": True, "fragment": "сделать ещё одну работу",
        "user_notion_id": "u", "chat_id": 100, "msg_id": None,
    })

    msg = _msg(1, "финансовый ритуал маше завтра")
    parsed_full = {
        "title": "Финансовый ритуал маше",
        "category": "✨ Ритуал", "priority": "Важно",
        "work_type": "🤝 Клиентская", "client_name": "Маша",
        "deadline": "2026-05-04", "reminder": None,
    }
    with patch.object(wp, "_parse_work_text",
                      AsyncMock(return_value=parsed_full)), \
         patch.object(wp, "get_user_tz", AsyncMock(return_value=3)), \
         patch.object(wp, "client_find", AsyncMock(return_value=None)):
        handled = await wp.handle_work_clarification(msg)

    assert handled is True
    # Старый partial дропнут, новое pending — полное превью
    pending = wp._pending_get(1)
    assert pending is not None
    assert pending.get("_partial") is not True
    assert pending["title"] == "Финансовый ритуал маше"
