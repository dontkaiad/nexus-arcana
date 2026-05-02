"""tests/test_ritual_preview.py — ritual_planned идёт через preview-flow,
запись в Notion НЕ создаётся пока Кай не нажмёт «Сохранить».
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
