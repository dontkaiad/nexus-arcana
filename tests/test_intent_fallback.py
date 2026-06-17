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
         patch("arcana.handlers.intent_resolve.ask_ritual_disambiguation",
               AsyncMock()) as disambig_mock, \
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
        "disambig": disambig_mock,
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
async def test_ritual_done_without_past_tense_becomes_ambiguous():
    """«сделать ритуал» в ritual_done → guard → ritual_ambiguous (переспрос).

    До фикса B6: guard конвертировал в ritual_planned → Works (тихая запись).
    После B6: ritual_ambiguous → ask_ritual_disambiguation.
    """
    res = await _route("ritual_done", "сделать маше финансовый ритуал")
    res["disambig"].assert_awaited_once()
    res["work"].assert_not_called()
    res["ritual"].assert_not_called()


@pytest.mark.asyncio
async def test_ritual_done_with_past_tense_stays_done():
    res = await _route("ritual_done", "провела маше финансовый ритуал вчера")
    res["ritual"].assert_awaited_once()
    res["work"].assert_not_called()


# ── Preview-flow для planned-интентов (бывший test_ritual_preview.py) ────────
# ritual_planned/session_planned идут через preview-flow, запись в Notion
# НЕ создаётся пока Кай не нажмёт «Сохранить».


@pytest.mark.asyncio
async def test_ritual_planned_routes_to_work_preview_no_immediate_write():
    """intent=ritual_planned для «сделать маше финансовый ритуал» →
    handle_add_work (= preview-flow), handle_add_ritual НЕ вызывается,
    work_add (запись в Notion) НЕ вызывается."""
    from arcana.handlers import base

    msg = _msg("сделать маше финансовый ритуал")
    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="ritual_planned")), \
         patch("arcana.handlers.works.handle_add_work",
               AsyncMock()) as work_mock, \
         patch("arcana.handlers.rituals.handle_add_ritual",
               AsyncMock()) as ritual_mock, \
         patch("core.notion_client.work_add", AsyncMock()) as add_mock, \
         patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search",
               AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False):
        await base.route_message(msg, user_notion_id="u")

    work_mock.assert_awaited_once()
    ritual_mock.assert_not_called()
    add_mock.assert_not_called()


@pytest.mark.asyncio
async def test_session_planned_also_routes_to_work_preview():
    from arcana.handlers import base

    msg = _msg("разложить на работу игоря")
    with patch("arcana.handlers.base.ask_claude",
               AsyncMock(return_value="session_planned")), \
         patch("arcana.handlers.works.handle_add_work",
               AsyncMock()) as work_mock, \
         patch("arcana.handlers.sessions.handle_add_session",
               AsyncMock()) as sess_mock, \
         patch("arcana.handlers.base.react", AsyncMock()), \
         patch("arcana.pending_clients.get_pending_client",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.grimoire.check_pending_search",
               AsyncMock(return_value=False)), \
         patch("arcana.pending_tarot.get_pending",
               AsyncMock(return_value=None)), \
         patch("arcana.handlers.work_preview.has_pending", return_value=False):
        await base.route_message(msg, user_notion_id="u")

    work_mock.assert_awaited_once()
    sess_mock.assert_not_called()
