"""core/login_tokens.py — bot-approval login flow (#23 follow-up).

Login via a Telegram Login Widget forces a phone-number fallback whenever the
browser has no live web.telegram.org session (desktop, incognito — see the
login.heylark.dev investigation). Instead: login.heylark.dev mints a token,
the user opens a t.me deep link to @heylark_booking_bot, Zarya asks "это
точно ты?" and the tap approves/denies — pure bot interaction, no phone step.

Shared `login_tokens` table lives in the `auth` Postgres DB (same DB as
`grants`/`invites`/`people`, owned by role `authsvc`) — login.heylark.dev
(heylark-infra, a separate repo) creates + polls rows via its own
`heylark_auth/tg_auth.py`; this module is Zarya's (nexus-arcana) side: look
up a pending token and approve/deny it. Neither side duplicates the other's
logic — both just read/write the same table directly, same pattern as
`core/auth_grants.py`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, MetaData, Table, Text, text
from sqlalchemy.engine import Engine

from core.auth_grants import get_auth_engine

logger = logging.getLogger("core.login_tokens")

_metadata = MetaData()
login_tokens = Table(
    "login_tokens", _metadata,
    Column("token", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("tg_id", BigInteger),
    Column("next_url", Text, nullable=False),
    Column("used", Boolean, nullable=False),
)

_TOKEN_TTL_SECONDS = 300  # 5 min — deep link + confirm tap should be fast


def _age_seconds(created_at) -> float:
    """created_at age in seconds — computed in Python (not SQL) so this works
    against both Postgres (tz-aware TIMESTAMPTZ, returned as datetime) and
    SQLite in tests (TEXT column, returned as a plain string)."""
    if created_at is None:
        return 0.0
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created_at).total_seconds()


def _get_pending_sync(engine: Engine, token: str) -> Optional[dict]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, tg_id, created_at FROM login_tokens WHERE token = :t"),
            {"t": token},
        ).mappings().first()
    if not row:
        return None
    return {"status": row["status"], "tg_id": row["tg_id"], "age_s": _age_seconds(row["created_at"])}


async def get_pending(token: str, *, engine: Optional[Engine] = None) -> Optional[dict]:
    """Look up a login token. None if unknown. 'expired' overrides a stale pending row."""
    engine = engine or get_auth_engine()
    if engine is None:
        return None
    row = await asyncio.to_thread(_get_pending_sync, engine, token)
    if row is None:
        return None
    if row["status"] == "pending" and row["age_s"] > _TOKEN_TTL_SECONDS:
        row["status"] = "expired"
    return row


def _set_status_sync(engine: Engine, token: str, status: str, tg_id: Optional[int]) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE login_tokens SET status = :s, tg_id = :tg "
                "WHERE token = :t AND status = 'pending'"
            ),
            {"s": status, "tg": tg_id, "t": token},
        )
        return result.rowcount > 0


async def approve(token: str, tg_id: int, *, engine: Optional[Engine] = None) -> bool:
    """Approve a pending token for tg_id. False if already decided/unknown/expired."""
    engine = engine or get_auth_engine()
    if engine is None:
        return False
    pending = await get_pending(token, engine=engine)
    if not pending or pending["status"] != "pending":
        return False
    return await asyncio.to_thread(_set_status_sync, engine, token, "approved", tg_id)


async def deny(token: str, tg_id: int, *, engine: Optional[Engine] = None) -> bool:
    engine = engine or get_auth_engine()
    if engine is None:
        return False
    return await asyncio.to_thread(_set_status_sync, engine, token, "denied", tg_id)
