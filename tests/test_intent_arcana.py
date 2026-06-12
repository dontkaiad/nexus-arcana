"""tests/test_intent_arcana.py — intent split в ROUTER_SYSTEM +
guard CLAUDE.md: бытовые задачи → Nexus (бывший test_arcana_redirect.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers.base import ROUTER_SYSTEM
from arcana.handlers.intent_resolve import (
    looks_like_practice,
    send_nexus_redirect,
)


def test_router_lists_split_intents():
    s = ROUTER_SYSTEM
    for it in (
        "session_done", "session_planned",
        "ritual_done", "ritual_planned", "ritual_ambiguous",
    ):
        assert it in s, f"intent missing: {it}"


def test_router_explains_planned_vs_done_for_rituals():
    """Промпт должен явно показывать, что глагол прошедшего времени → done."""
    s = ROUTER_SYSTEM
    assert "сделала" in s
    assert "провела" in s
    assert "запланировать" in s


def test_router_documents_ambiguous_case():
    """Промпт описывает кейс неоднозначности — без глагола времени, без структуры."""
    s = ROUTER_SYSTEM
    assert "неоднозначно" in s.lower() or "ambiguous" in s.lower() \
        or "ritual_ambiguous" in s
    assert "переспросить" in s or "переспрос" in s


def test_dispatch_includes_planned_and_done():
    """В route_message dispatch должны быть entry для planned и done."""
    import inspect
    from arcana.handlers import base
    src = inspect.getsource(base)
    assert '"session_planned"' in src
    assert '"session_done"' in src
    assert '"ritual_planned"' in src
    assert '"ritual_done"' in src


# ── Guard: бытовые задачи → Nexus (бывший test_arcana_redirect.py) ───────────
# Аркана не должна сохранять «сделать миниапп про котов» в 🔮 Работы —
# это бытовуха, её место в Nexus. Guard ловит intent=work/ritual_planned/
# session_planned без эзотерических маркеров и переспрашивает Кай.


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
    """intent=work без практических маркеров теперь нормализуется в
    nexus_redirect и сразу шлёт сообщение редиректа (без переспроса)."""
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
         patch("arcana.handlers.intent_resolve.send_nexus_redirect",
               AsyncMock()) as redirect_mock, \
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

    redirect_mock.assert_awaited_once()
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
