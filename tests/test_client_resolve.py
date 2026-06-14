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
    from arcana.handlers import sessions as sess
    from arcana.handlers.sessions import _create_resolved_client
    from core.notion_client import CLIENT_TYPE_PAID

    with patch.object(sess._client_repo, "add", AsyncMock(return_value="new-page-id")):
        res = await _create_resolved_client("u1", "Маша", CLIENT_TYPE_PAID)

    assert res == ("new-page-id", "Маша")


@pytest.mark.asyncio
async def test_create_resolved_client_free():
    from arcana.handlers import sessions as sess
    from arcana.handlers.sessions import _create_resolved_client
    from core.notion_client import CLIENT_TYPE_FREE

    with patch.object(sess._client_repo, "add", AsyncMock(return_value="p2")):
        res = await _create_resolved_client("u1", "Аня", CLIENT_TYPE_FREE)

    assert res == ("p2", "Аня")
