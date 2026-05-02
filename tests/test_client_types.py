"""tests/test_client_types.py — типы клиентов и should_skip_payment."""
from unittest.mock import AsyncMock, patch

import pytest

from core.notion_client import (
    CLIENT_TYPE_FREE, CLIENT_TYPE_PAID, CLIENT_TYPE_SELF,
    should_skip_payment,
)


def test_should_skip_self_and_free():
    assert should_skip_payment(CLIENT_TYPE_SELF) is True
    assert should_skip_payment(CLIENT_TYPE_FREE) is True


def test_should_not_skip_paid():
    assert should_skip_payment(CLIENT_TYPE_PAID) is False


def test_should_not_skip_none_or_unknown():
    assert should_skip_payment(None) is False
    assert should_skip_payment("") is False
    assert should_skip_payment("🌗 Странный") is False


@pytest.mark.asyncio
async def test_client_add_default_paid():
    from core.notion_client import client_add

    captured: dict = {}

    async def fake_create(db_id, props):
        captured["props"] = props
        return "p1"

    with patch("core.notion_client.page_create", new=fake_create):
        await client_add(name="Маша")

    assert captured["props"]["Тип клиента"]["select"]["name"] == CLIENT_TYPE_PAID


@pytest.mark.asyncio
async def test_client_add_explicit_free():
    from core.notion_client import client_add

    captured: dict = {}

    async def fake_create(db_id, props):
        captured["props"] = props
        return "p2"

    with patch("core.notion_client.page_create", new=fake_create):
        await client_add(name="Аня", client_type=CLIENT_TYPE_FREE)

    assert captured["props"]["Тип клиента"]["select"]["name"] == CLIENT_TYPE_FREE


@pytest.mark.asyncio
async def test_client_add_falls_back_when_field_missing():
    from core.notion_client import client_add

    calls: dict = {"n": 0}
    captured: list = []

    async def fake_create(db_id, props):
        captured.append(dict(props))
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("validation_error: Тип клиента is not a property")
        return "p3"

    with patch("core.notion_client.page_create", new=fake_create):
        pid = await client_add(name="Лена", client_type=CLIENT_TYPE_FREE)

    assert pid == "p3"
    assert "Тип клиента" in captured[0]
    assert "Тип клиента" not in captured[1]


@pytest.mark.asyncio
async def test_resolve_self_uses_type_filter():
    """resolve_self_client сначала пробует select-filter Тип клиента = 🌟 Self."""
    from core.notion_client import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    captured_filters: list = []

    async def fake_query(db_id, filters=None, sorts=None, page_size=20):
        captured_filters.append(filters)
        # Первый вызов (select=Self) — есть результат
        if len(captured_filters) == 1:
            return [{"id": "kai-self", "properties": {}}]
        return []

    with patch("core.notion_client.query_pages", new=fake_query):
        cid = await resolve_self_client(user_notion_id="u1")
    assert cid == "kai-self"
    # Должен был один раз спросить именно по типу клиента.
    f = captured_filters[0]
    if isinstance(f, dict) and "and" in f:
        # _with_user_filter может обернуть в and
        sub = f["and"][0]
    else:
        sub = f
    assert sub.get("property") == "Тип клиента"
    assert sub.get("select", {}).get("equals") == CLIENT_TYPE_SELF
