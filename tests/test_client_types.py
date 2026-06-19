"""tests/test_client_types.py — типы клиентов и should_skip_payment."""
from unittest.mock import AsyncMock, patch

import pytest

from core.client_resolve import (
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
async def test_resolve_self_uses_type_filter():
    """resolve_self_client находит self-клиента через PgClientsRepo.find_self."""
    from core.client_resolve import resolve_self_client, _SELF_CLIENT_CACHE
    from arcana.repos.clients_repo import Client

    _SELF_CLIENT_CACHE.clear()
    fake_client = Client(id="kai-self", name="Кай", contact="", request="", notes="", since="")

    with patch(
        "arcana.repos.pg_clients_repo.PgClientsRepo.find_self",
        AsyncMock(return_value=fake_client),
    ):
        cid = await resolve_self_client(user_notion_id="u1")
    assert cid == "kai-self"
