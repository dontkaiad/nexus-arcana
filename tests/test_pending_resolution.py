"""tests/test_pending_resolution.py — резолюция pending vs новое сообщение.

Баг: любое сообщение с активным pending → «Не поняла уточнение». Фикс:
эвристики looks_like_deadline_clarification / looks_like_new_intent
маршрутизируют корректно, ambiguous-кейс → переспрос.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import arcana.handlers.work_preview as wp

_TMP_DB = tempfile.NamedTemporaryFile(
    suffix="_pending_works_resolution.db", delete=False
).name
wp._PENDING_DB = _TMP_DB


def _fresh_db():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


# ── Эвристики ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("завтра", True),
    ("через 2 дня", True),
    ("в 10:00", True),
    ("1 июня", True),
    ("в пятницу в 18", True),
    ("дедлайн понедельник", True),
    ("без напоминания", True),
    ("сделать миниапп про котов", False),
    ("позвонить отцу", False),
    ("купить хлеб", False),
])
def test_looks_like_deadline_clarification(text, expected):
    assert wp.looks_like_deadline_clarification(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("сделать миниапп про котов", True),
    ("позвонить отцу", True),
    ("купить хлеб", True),
    ("разложить на работу игоря", True),
    ("ритуал для маши", True),
    ("завтра", False),
    ("через 2 дня", False),
    ("в 10:00", False),
])
def test_looks_like_new_intent(text, expected):
    assert wp.looks_like_new_intent(text) is expected


# ── handle_work_clarification: ветвление ────────────────────────────────────

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


def _seed_pending(uid: int = 7) -> None:
    slug = wp._make_slug(uid)
    wp._pending_set(uid, slug, {
        "title": "Старая работа", "category": "✨ Ритуал",
        "priority": "Важно", "work_type": "🌟 Личная",
        "client_name": None, "client_id": None,
        "deadline": None, "reminder": None,
        "msg_id": 999, "chat_id": 100, "user_notion_id": "u",
    })


@pytest.mark.asyncio
async def test_deadline_text_parsed_as_clarification():
    _fresh_db()
    _seed_pending(7)
    msg = _msg(7, "завтра")
    with patch.object(wp, "_parse_clarification",
                      AsyncMock(return_value={"deadline": "2026-05-04"})), \
         patch.object(wp, "get_user_tz", AsyncMock(return_value=3)), \
         patch("core.utils.react", AsyncMock()):
        handled = await wp.handle_work_clarification(msg)
    assert handled is True
    assert wp._pending_get(7)["deadline"] == "2026-05-04"


@pytest.mark.asyncio
async def test_new_intent_drops_pending_and_returns_false():
    _fresh_db()
    _seed_pending(7)
    msg = _msg(7, "сделать миниапп про котов")
    handled = await wp.handle_work_clarification(msg)
    assert handled is False
    assert wp._pending_get(7) is None


@pytest.mark.asyncio
async def test_new_intent_phone_call_drops_pending():
    _fresh_db()
    _seed_pending(7)
    msg = _msg(7, "позвонить отцу")
    handled = await wp.handle_work_clarification(msg)
    assert handled is False
    assert wp._pending_get(7) is None


@pytest.mark.asyncio
async def test_ambiguous_text_asks_clarify_or_new():
    _fresh_db()
    _seed_pending(7)
    msg = _msg(7, "ну такое")
    fake_ask = AsyncMock()
    with patch("arcana.handlers.intent_resolve.ask_clarify_or_new", fake_ask):
        handled = await wp.handle_work_clarification(msg)
    assert handled is True
    fake_ask.assert_awaited_once()
    # Pending не дропнут — ждём решения
    assert wp._pending_get(7) is not None


@pytest.mark.asyncio
async def test_ttl_expired_pending_is_dropped_silently():
    _fresh_db()
    slug = wp._make_slug(7)
    # Пишем pending с устаревшим timestamp
    import json as _json
    import sqlite3 as _sq
    import time as _t
    with _sq.connect(wp._PENDING_DB) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS pending "
            "(uid INTEGER PRIMARY KEY, slug TEXT, data TEXT, ts REAL)"
        )
        con.execute(
            "INSERT OR REPLACE INTO pending (uid, slug, data, ts) VALUES (?,?,?,?)",
            (7, slug, _json.dumps({"title": "X"}), _t.time() - wp._PENDING_TTL - 10),
        )
    # _pending_get должен вернуть None и удалить запись
    assert wp._pending_get(7) is None
    msg = _msg(7, "что угодно")
    handled = await wp.handle_work_clarification(msg)
    assert handled is False
