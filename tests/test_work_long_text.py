"""Длинный ввод работы: обрезанный JSON парсера не должен давать «Что за работа?»."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import arcana.handlers.work_preview as wp

_TMP_DB = tempfile.NamedTemporaryFile(suffix="_pending_works_long.db", delete=False).name
wp._PENDING_DB = _TMP_DB

HEAD = "ритуал на открытие дорог через род сегодня в 19:45 (день помина предков)"
LONG = HEAD + "\n" + ("шаг: зажечь свечу, назвать предков по имени. " * 45) + "\nмолитва: " + ("слова " * 40)
TRUNCATED = '{"title": "Ритуал на открытие дорог", "deadline": "2026-11-01T19:45", "category": "ритуал", "notes": "шаг: зажечь свечу, наз'


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.from_user.id = 77
    m.chat.id = 100
    m.text = text
    answered = MagicMock()
    answered.message_id = 999
    answered.chat.id = 100
    m.answer = AsyncMock(return_value=answered)
    return m


def _fresh():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


async def _run(llm_raw: str, text: str):
    _fresh()
    msg = _msg(text)
    with patch.object(wp, "ask_claude", AsyncMock(return_value=llm_raw)), \
         patch.object(wp, "get_effective_tz", AsyncMock(return_value=(3, "UTC+3"))):
        await wp.handle_add_work_preview(msg, text, "u")
    return msg


@pytest.mark.asyncio
async def test_truncated_json_on_long_text_gives_preview_not_partial():
    assert len(LONG) > 1500
    msg = await _run(TRUNCATED, LONG)
    sent = msg.answer.await_args.args[0]
    assert "Что за работа" not in sent
    assert "Ритуал" in sent  # категория из WORK_CATEGORY_MAP
    assert "Личная" in sent
    data = wp._pending_get(77)
    assert data and not data.get("_partial")
    assert data["title"] == HEAD[:80]
    assert data["notes"] == LONG.strip()


@pytest.mark.asyncio
async def test_short_garbage_still_asks_clarification():
    msg = await _run("not json", "сделать штуку")
    assert "Что за работа" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_llm_gets_only_head_and_tail_goes_to_notes():
    _fresh()
    ok = '{"title":"Ритуал","deadline":"2026-11-01T19:45","category":"ритуал","type":"личная","notes":null}'
    mock = AsyncMock(return_value=ok)
    with patch.object(wp, "ask_claude", mock), \
         patch.object(wp, "get_effective_tz", AsyncMock(return_value=(3, "UTC+3"))):
        data = await wp._parse_work_text(LONG, 3)
    assert len(mock.await_args.args[0]) <= 400
    assert mock.await_args.kwargs["max_tokens"] == 600
    assert data["notes"].endswith(LONG[400:].strip())
    assert data["category"] == "✨ Ритуал"
    assert data["work_type"] == "🌟 Личная"
