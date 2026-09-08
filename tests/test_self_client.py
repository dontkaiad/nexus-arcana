"""tests/test_self_client.py — авто-резолв «Кай (личный)» в self-client."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_client(pg_id: str):
    from arcana.repos.clients_repo import Client
    return Client(id=pg_id, name="Кай", contact="", request="", notes="", since="")


@pytest.mark.asyncio
async def test_resolve_self_client_finds_lichniy():
    from core.client_resolve import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    with patch(
        "arcana.repos.pg_clients_repo.PgClientsRepo.find_self",
        AsyncMock(return_value=_make_client("kai-self-id")),
    ):
        cid = await resolve_self_client(user_id="u1")
    assert cid == "kai-self-id"


@pytest.mark.asyncio
async def test_resolve_self_client_caches_result():
    """Повторный вызов не должен идти в PG — id берётся из process-кеша."""
    from core.client_resolve import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    mock = AsyncMock(return_value=_make_client("kai-id"))
    with patch("arcana.repos.pg_clients_repo.PgClientsRepo.find_self", mock):
        cid1 = await resolve_self_client(user_id="u1")
        cid2 = await resolve_self_client(user_id="u1")
    assert cid1 == cid2 == "kai-id"
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_resolve_self_client_creates_when_not_found():
    """#154 стадия 1: self-карточки нет → создаём «Кай» (🌟 Self)."""
    from core.client_resolve import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    create = AsyncMock(return_value=77)
    with patch(
        "arcana.repos.pg_clients_repo.PgClientsRepo.find_self",
        AsyncMock(return_value=None),
    ), patch(
        "arcana.repos.pg_clients_repo.PgClientsRepo.create", create,
    ):
        cid = await resolve_self_client(user_id="u-missing")
    assert cid == "77"
    kw = create.await_args.kwargs
    assert kw["name"] == "Кай" and kw["type_code"] == "self"


@pytest.mark.asyncio
async def test_resolve_self_client_none_when_create_fails():
    from core.client_resolve import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    with patch(
        "arcana.repos.pg_clients_repo.PgClientsRepo.find_self",
        AsyncMock(return_value=None),
    ), patch(
        "arcana.repos.pg_clients_repo.PgClientsRepo.create",
        AsyncMock(return_value=None),
    ):
        cid = await resolve_self_client(user_id="u-missing")
    assert cid is None


@pytest.mark.asyncio
async def test_resolve_self_client_isolated_per_user():
    from core.client_resolve import resolve_self_client, _SELF_CLIENT_CACHE

    _SELF_CLIENT_CACHE.clear()
    sequence = [_make_client("u1-self"), _make_client("u2-self")]
    call_count = {"n": 0}

    async def fake_find_self(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return sequence[idx]

    with patch("arcana.repos.pg_clients_repo.PgClientsRepo.find_self", fake_find_self):
        a = await resolve_self_client(user_id="u1")
        b = await resolve_self_client(user_id="u2")
    assert a == "u1-self"
    assert b == "u2-self"
