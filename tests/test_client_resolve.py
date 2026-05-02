"""tests/test_client_resolve.py — resolve-dialog для multi-flow."""
from unittest.mock import AsyncMock, patch

import pytest


def test_resolve_dialog_kb_layout():
    from arcana.handlers.sessions import _resolve_dialog_kb
    kb = _resolve_dialog_kb("abc123")
    # 3 ряда: paid; free; self+cancel
    assert len(kb.inline_keyboard) == 3
    assert "paid" in kb.inline_keyboard[0][0].callback_data
    assert "free" in kb.inline_keyboard[1][0].callback_data
    assert "self" in kb.inline_keyboard[2][0].callback_data
    assert "cancel" in kb.inline_keyboard[2][1].callback_data


def test_short_resolve_slug_unique():
    from arcana.handlers.sessions import _short_resolve_slug
    s1 = _short_resolve_slug()
    s2 = _short_resolve_slug()
    assert s1 != s2
    assert len(s1) == 16


@pytest.mark.asyncio
async def test_create_resolved_client_paid():
    from arcana.handlers.sessions import _create_resolved_client
    from core.notion_client import CLIENT_TYPE_PAID

    captured: dict = {}

    async def fake_client_add(name, date=None, user_notion_id="", contact="",
                              request="", client_type=None):
        captured["name"] = name
        captured["client_type"] = client_type
        return "new-page-id"

    with patch("core.notion_client.client_add", new=fake_client_add):
        res = await _create_resolved_client("u1", "Маша", CLIENT_TYPE_PAID)

    assert res == ("new-page-id", "Маша")
    assert captured["client_type"] == CLIENT_TYPE_PAID


@pytest.mark.asyncio
async def test_create_resolved_client_free():
    from arcana.handlers.sessions import _create_resolved_client
    from core.notion_client import CLIENT_TYPE_FREE

    captured: dict = {}

    async def fake_client_add(name, date=None, user_notion_id="", contact="",
                              request="", client_type=None):
        captured["client_type"] = client_type
        return "p2"

    with patch("core.notion_client.client_add", new=fake_client_add):
        res = await _create_resolved_client("u1", "Аня", CLIENT_TYPE_FREE)

    assert res == ("p2", "Аня")
    assert captured["client_type"] == CLIENT_TYPE_FREE
