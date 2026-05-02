"""tests/test_intent_fallback.py — нормализация legacy intent (work/task) и
guard на ritual_done без прошедшего времени.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.from_user.id = 42
    m.chat.id = 1
    m.text = text
    m.caption = None
    m.photo = None
    m.reply_to_message = None
    m.answer = AsyncMock()
    return m


async def _route(intent: str, text: str):
    """Прогоняет route_message с заданным intent от классификатора."""
    from arcana.handlers import base
    msg = _msg(text)
    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value=intent)), \
         patch("arcana.handlers.intent_resolve.send_nexus_redirect",
               AsyncMock()) as redirect_mock, \
         patch("arcana.handlers.works.handle_add_work",
               AsyncMock()) as work_mock, \
         patch("arcana.handlers.rituals.handle_add_ritual",
               AsyncMock()) as ritual_mock, \
         patch("arcana.handlers.intent_resolve.ask_practice_or_nexus",
               AsyncMock()) as practice_ask_mock, \
         patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search",
               AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False):
        await base.route_message(msg, user_notion_id="u")
    return {
        "redirect": redirect_mock,
        "work": work_mock,
        "ritual": ritual_mock,
        "practice_ask": practice_ask_mock,
    }


@pytest.mark.asyncio
async def test_intent_work_without_practice_redirects_to_nexus():
    res = await _route("work", "сделать миниапп про котов")
    res["redirect"].assert_awaited_once()
    res["work"].assert_not_called()


@pytest.mark.asyncio
async def test_intent_work_with_ritual_keyword_routes_to_ritual_planned():
    res = await _route("work", "сделать ритуал маше")
    # Ritual_planned → handle_add_work (preview-flow), редиректа нет
    res["work"].assert_awaited_once()
    res["redirect"].assert_not_called()


@pytest.mark.asyncio
async def test_intent_task_legacy_alias_redirects():
    res = await _route("task", "позвонить отцу")
    res["redirect"].assert_awaited_once()


@pytest.mark.asyncio
async def test_ritual_done_without_past_tense_becomes_planned():
    """«сделать ритуал» в ritual_done → guard переводит в planned (preview)."""
    res = await _route("ritual_done", "сделать маше финансовый ритуал")
    # ritual_planned → handle_add_work (preview)
    res["work"].assert_awaited_once()
    # handle_add_ritual (мгновенная запись) НЕ должен быть вызван
    res["ritual"].assert_not_called()


@pytest.mark.asyncio
async def test_ritual_done_with_past_tense_stays_done():
    res = await _route("ritual_done", "провела маше финансовый ритуал вчера")
    res["ritual"].assert_awaited_once()
    res["work"].assert_not_called()
