"""tests/test_self_client.py — авто-резолв «Кай (личный)» в self-client."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_resolve_self_client_finds_lichniy():
    from core.notion_client import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    fake_results = [{"id": "kai-self-id", "properties": {}}]
    with patch(
        "core.notion_client.query_pages", new=AsyncMock(return_value=fake_results)
    ):
        cid = await resolve_self_client(user_notion_id="u1")
    assert cid == "kai-self-id"


@pytest.mark.asyncio
async def test_resolve_self_client_caches_result():
    """Повторный вызов не должен идти в Notion — id берётся из process-кеша."""
    from core.notion_client import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    mock = AsyncMock(return_value=[{"id": "kai-id", "properties": {}}])
    with patch("core.notion_client.query_pages", new=mock):
        cid1 = await resolve_self_client(user_notion_id="u1")
        cid2 = await resolve_self_client(user_notion_id="u1")
    assert cid1 == cid2 == "kai-id"
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_resolve_self_client_returns_none_when_not_found():
    from core.notion_client import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    with patch(
        "core.notion_client.query_pages", new=AsyncMock(return_value=[])
    ):
        cid = await resolve_self_client(user_notion_id="u-missing")
    assert cid is None


@pytest.mark.asyncio
async def test_resolve_self_client_isolated_per_user():
    from core.notion_client import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    sequence = [
        [{"id": "u1-self", "properties": {}}],
        [{"id": "u2-self", "properties": {}}],
    ]
    call_count = {"n": 0}

    async def fake_query(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return sequence[idx]

    with patch("core.notion_client.query_pages", new=fake_query):
        a = await resolve_self_client(user_notion_id="u1")
        b = await resolve_self_client(user_notion_id="u2")
    assert a == "u1-self"
    assert b == "u2-self"
