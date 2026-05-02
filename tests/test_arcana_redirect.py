"""tests/test_arcana_redirect.py — guard CLAUDE.md: бытовые задачи → Nexus.

Аркана не должна сохранять «сделать миниапп про котов» в 🔮 Работы —
это бытовуха, её место в Nexus. Guard ловит intent=work/ritual_planned/
session_planned без эзотерических маркеров и переспрашивает Кай.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers.intent_resolve import (
    looks_like_practice,
    send_nexus_redirect,
)


# ── Маркер-эвристика ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("сделать миниапп про котов", False),
    ("позвонить маме", False),
    ("купить хлеб", False),
    ("записаться к врачу", False),
    ("сделать маше финансовый ритуал", True),
    ("разложить на работу игоря", True),
    ("закупить свечи", True),
    ("очистить квартиру защитным ритуалом", True),
    ("приворот для Анны", True),
    ("записать в гримуар", True),
    ("подготовить колоду", True),
])
def test_looks_like_practice(text, expected):
    assert looks_like_practice(text) is expected


# ── Сообщение редиректа ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_nexus_redirect_format():
    msg = MagicMock()
    msg.answer = AsyncMock()
    with patch("core.utils.react", AsyncMock()):
        await send_nexus_redirect(msg, "сделать миниапп про котов")
    msg.answer.assert_awaited_once()
    sent = msg.answer.await_args.args[0]
    assert "@nexus_kailark_bot" in sent
    assert "сделать миниапп про котов" in sent
    assert "практик" in sent.lower()


# ── Guard в base.py: dispatch не вызывает handle_add_work ────────────────────

@pytest.mark.asyncio
async def test_guard_redirects_non_practice_work_intent():
    """intent=work без практических маркеров → ask_practice_or_nexus,
    handle_add_work НЕ вызван."""
    from arcana.handlers import base

    msg = MagicMock()
    msg.from_user.id = 42
    msg.chat.id = 1
    msg.text = "сделать миниапп про котов"
    msg.caption = None
    msg.photo = None
    msg.reply_to_message = None
    msg.answer = AsyncMock()

    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="work")), \
         patch("arcana.handlers.intent_resolve.ask_practice_or_nexus",
               AsyncMock()) as ask_mock, \
         patch("arcana.handlers.works.handle_add_work",
               AsyncMock()) as add_mock, \
         patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search",
               AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False):
        await base.route_message(msg, user_notion_id="u")

    ask_mock.assert_awaited_once()
    add_mock.assert_not_called()


@pytest.mark.asyncio
async def test_guard_lets_practice_work_through():
    """intent=work с эзотерикой → handle_add_work вызван, redirect — нет."""
    from arcana.handlers import base

    msg = MagicMock()
    msg.from_user.id = 42
    msg.chat.id = 1
    msg.text = "закупить свечи на следующую неделю"
    msg.caption = None
    msg.photo = None
    msg.reply_to_message = None
    msg.answer = AsyncMock()

    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="work")), \
         patch("arcana.handlers.intent_resolve.ask_practice_or_nexus",
               AsyncMock()) as ask_mock, \
         patch("arcana.handlers.works.handle_add_work",
               AsyncMock()) as add_mock, \
         patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search",
               AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False):
        await base.route_message(msg, user_notion_id="u")

    ask_mock.assert_not_called()
    add_mock.assert_awaited_once()
