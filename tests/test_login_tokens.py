"""tests/test_login_tokens.py — core.login_tokens (#23 follow-up).

Zarya's side of the bot-approval login flow: approve/deny a pending token
minted by login.heylark.dev (a separate repo — see heylark-infra's
heylark_auth/tg_auth.py for the create/poll half). Local SQLite injected as
the auth engine, same pattern as tests/test_auth_grants.py.
"""
from __future__ import annotations

import sqlalchemy as sa
import pytest
from sqlalchemy.pool import StaticPool

from core.login_tokens import approve, deny, get_pending

TG_ID = 67686090


def _make_engine():
    eng = sa.create_engine("sqlite:///:memory:",
                            connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE login_tokens (token TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "tg_id BIGINT, next_url TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT "
            "CURRENT_TIMESTAMP, used BOOLEAN NOT NULL DEFAULT 0)"
        ))
    return eng


def _seed(eng, token, status="pending", age_seconds=0, next_url=""):
    with eng.begin() as c:
        c.execute(sa.text(
            "INSERT INTO login_tokens (token, status, next_url, created_at) "
            "VALUES (:t, :s, :n, datetime('now', :age))"
        ), {"t": token, "s": status, "n": next_url, "age": f"-{age_seconds} seconds"})


@pytest.mark.asyncio
async def test_get_pending_unknown_token_returns_none():
    eng = _make_engine()
    assert await get_pending("nope", engine=eng) is None


@pytest.mark.asyncio
async def test_get_pending_returns_status():
    eng = _make_engine()
    _seed(eng, "tok1")
    row = await get_pending("tok1", engine=eng)
    assert row["status"] == "pending"
    assert row["tg_id"] is None


@pytest.mark.asyncio
async def test_get_pending_stale_row_reads_expired():
    eng = _make_engine()
    _seed(eng, "tok1", age_seconds=600)  # past the 300s TTL
    row = await get_pending("tok1", engine=eng)
    assert row["status"] == "expired"


@pytest.mark.asyncio
async def test_approve_flips_status_and_sets_tg_id():
    eng = _make_engine()
    _seed(eng, "tok1")
    ok = await approve("tok1", TG_ID, engine=eng)
    assert ok is True
    row = await get_pending("tok1", engine=eng)
    assert row["status"] == "approved"
    assert row["tg_id"] == TG_ID


@pytest.mark.asyncio
async def test_approve_unknown_token_fails():
    eng = _make_engine()
    assert await approve("nope", TG_ID, engine=eng) is False


@pytest.mark.asyncio
async def test_approve_expired_token_fails():
    eng = _make_engine()
    _seed(eng, "tok1", age_seconds=600)
    assert await approve("tok1", TG_ID, engine=eng) is False


@pytest.mark.asyncio
async def test_approve_already_decided_token_fails():
    eng = _make_engine()
    _seed(eng, "tok1", status="denied")
    assert await approve("tok1", TG_ID, engine=eng) is False


@pytest.mark.asyncio
async def test_deny_flips_status():
    eng = _make_engine()
    _seed(eng, "tok1")
    ok = await deny("tok1", TG_ID, engine=eng)
    assert ok is True
    row = await get_pending("tok1", engine=eng)
    assert row["status"] == "denied"
