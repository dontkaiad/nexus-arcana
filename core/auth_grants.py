"""core/auth_grants.py — role resolution against the shared heylark auth DB (#23 / ADR-0026).

The `grants` table lives in a SEPARATE Postgres database (`AUTH_DATABASE_URL`,
db `auth`), shared across heylark products (`cats`, and now `booking`). It is
owned by `authsvc`; this module needs `CONNECT` on that DB plus
`SELECT/INSERT/UPDATE` on `grants`.

Role model (ADR-0012 / ADR-0026):
- **admin**  — a hardcoded owner (`config.allowed_ids`); no grant row.
- **friend** — an `approved` grant row for the app.
- **guest**  — everyone else (default).

Roles are keyed on the sender's `tg_id`, NEVER on Telegram-group membership.
If `AUTH_DATABASE_URL` is unset (local dev / tests without an auth DB), every
non-admin resolves to `guest`.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Column, MetaData, Table, Text, create_engine, select, text,
)
from sqlalchemy.engine import Engine

logger = logging.getLogger("core.auth_grants")

_metadata = MetaData()
grants = Table(
    "grants", _metadata,
    Column("tg_id", BigInteger, primary_key=True),
    Column("app", Text, primary_key=True),
    Column("role", Text),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

_auth_engine: Optional[Engine] = None


def get_auth_engine() -> Optional[Engine]:
    """Engine for the shared auth DB, or None when AUTH_DATABASE_URL is unset."""
    global _auth_engine
    if _auth_engine is None:
        url = os.environ.get("AUTH_DATABASE_URL", "").strip()
        if not url:
            return None
        _auth_engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
        logger.info("auth-DB engine created: %s", url.split("@")[-1])
    return _auth_engine


def _approved_role_sync(engine: Engine, tg_id: int, app: str) -> Optional[str]:
    q = (
        select(grants.c.role)
        .where(grants.c.tg_id == tg_id)
        .where(grants.c.app == app)
        .where(grants.c.status == "approved")
    )
    with engine.connect() as conn:
        row = conn.execute(q).first()
    return (row[0] if row else None)


async def grant_role(tg_id: int, app: str, *, engine: Optional[Engine] = None) -> Optional[str]:
    """Return the `role` of an approved grant for (tg_id, app), else None."""
    eng = engine or get_auth_engine()
    if eng is None:
        return None
    try:
        return await asyncio.to_thread(_approved_role_sync, eng, tg_id, app)
    except Exception as e:  # auth DB down / not reachable — fail closed
        logger.warning("grant_role(%s, %s) failed: %s", tg_id, app, e)
        return None


async def booking_role(tg_id: int, *, engine: Optional[Engine] = None) -> str:
    """'admin' | 'friend' | 'guest' for the booking app."""
    from core.config import config
    if tg_id and tg_id in config.allowed_ids:
        return "admin"
    role = await grant_role(tg_id, "booking", engine=engine)
    if role == "admin":
        return "admin"
    return "friend" if role else "guest"


def _upsert_grant_sync(engine: Engine, tg_id: int, app: str, role: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    # Postgres UPSERT on (tg_id, app). SQLite in tests understands the same form.
    stmt = text(
        """
        INSERT INTO grants (tg_id, app, role, status, created_at, updated_at)
        VALUES (:tg, :app, :role, :status, :now, :now)
        ON CONFLICT (tg_id, app)
        DO UPDATE SET role = :role, status = :status, updated_at = :now
        """
    )
    with engine.begin() as conn:
        conn.execute(stmt, {"tg": tg_id, "app": app, "role": role, "status": status, "now": now})


async def upsert_booking_grant(
    tg_id: int,
    role: str = "friend",
    status: str = "approved",
    *,
    engine: Optional[Engine] = None,
) -> bool:
    """Grant (or update) booking access for a tg_id. Returns False if no auth DB."""
    eng = engine or get_auth_engine()
    if eng is None:
        logger.warning("upsert_booking_grant: AUTH_DATABASE_URL unset")
        return False
    await asyncio.to_thread(_upsert_grant_sync, eng, tg_id, "booking", role, status)
    return True
