"""tests/test_auth_grants.py — core.auth_grants role resolution (#23 B1 / ADR-0026).

Local SQLite `grants` table injected as the auth engine.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.auth_grants import calendar_role, grant_role, upsert_calendar_grant

OWNER_A, OWNER_B = 111, 222
FRIEND = 333
STRANGER = 444


def _make_engine(seed_calendar_friend=True):
    eng = sa.create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE grants (tg_id BIGINT NOT NULL, app TEXT NOT NULL, role TEXT, "
            "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "PRIMARY KEY (tg_id, app))"))
        c.execute(sa.text(
            "INSERT INTO grants VALUES (999, 'cats', 'resident', 'approved', 'x', 'x')"))
        if seed_calendar_friend:
            c.execute(sa.text(
                "INSERT INTO grants VALUES (:t, 'calendar', 'friend', 'approved', 'x', 'x')"),
                {"t": FRIEND})
            c.execute(sa.text(
                "INSERT INTO grants VALUES (555, 'calendar', 'friend', 'pending', 'x', 'x')"))
    return eng


@pytest.fixture
def _cfg():
    with patch("core.config.config.allowed_ids", [OWNER_A, OWNER_B]):
        yield


@pytest.mark.asyncio
async def test_owner_is_admin_without_grant_row(_cfg):
    eng = _make_engine()
    assert await calendar_role(OWNER_A, engine=eng) == "admin"
    assert await calendar_role(OWNER_B, engine=eng) == "admin"


@pytest.mark.asyncio
async def test_approved_calendar_grant_is_friend(_cfg):
    eng = _make_engine()
    assert await calendar_role(FRIEND, engine=eng) == "friend"


@pytest.mark.asyncio
async def test_pending_or_missing_grant_is_guest(_cfg):
    eng = _make_engine()
    assert await calendar_role(555, engine=eng) == "guest"       # pending
    assert await calendar_role(STRANGER, engine=eng) == "guest"  # no row


@pytest.mark.asyncio
async def test_cats_grant_does_not_grant_calendar(_cfg):
    eng = _make_engine()
    assert await calendar_role(999, engine=eng) == "guest"
    assert await grant_role(999, "cats", engine=eng) == "resident"


@pytest.mark.asyncio
async def test_no_auth_engine_everyone_is_guest(_cfg):
    # engine=None and AUTH_DATABASE_URL unset
    with patch("core.auth_grants.get_auth_engine", return_value=None):
        assert await calendar_role(FRIEND) == "guest"
        assert await calendar_role(OWNER_A) == "admin"  # owner still works


@pytest.mark.asyncio
async def test_upsert_grant_creates_and_updates(_cfg):
    eng = _make_engine(seed_calendar_friend=False)
    assert await calendar_role(FRIEND, engine=eng) == "guest"
    assert await upsert_calendar_grant(FRIEND, engine=eng) is True
    assert await calendar_role(FRIEND, engine=eng) == "friend"
    # idempotent re-grant
    assert await upsert_calendar_grant(FRIEND, engine=eng) is True
    with eng.connect() as c:
        n = c.execute(sa.text("SELECT COUNT(*) FROM grants WHERE tg_id=:t AND app='calendar'"),
                      {"t": FRIEND}).scalar()
    assert n == 1
