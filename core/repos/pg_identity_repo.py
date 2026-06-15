"""core/repos/pg_identity_repo.py — PG implementation for 🪪 Пользователи (identity domain).

IdentityUser is the domain object; PgIdentityRepo wraps core_identity table.
All async methods use asyncio.to_thread over sync SQLAlchemy (no asyncpg).

notion_id is the natural key, matching user_notion_id TEXT in all other PG tables.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select

from core.repos.identity_table import core_identity

logger = logging.getLogger("core.pg_identity_repo")


def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


# ── Domain object ──────────────────────────────────────────────────────────────

@dataclass
class IdentityUser:
    """Domain representation of a Пользователи record."""
    notion_id: str = ""
    tg_id: int = 0
    name: str = ""
    role: str = ""
    perm_nexus: bool = False
    perm_arcana: bool = False
    perm_finance: bool = False


def _row_to_user(row) -> IdentityUser:
    return IdentityUser(
        notion_id=row.notion_id or "",
        tg_id=int(row.tg_id or 0),
        name=row.name or "",
        role=row.role or "",
        perm_nexus=bool(row.perm_nexus),
        perm_arcana=bool(row.perm_arcana),
        perm_finance=bool(row.perm_finance),
    )


# ── Sync helpers ───────────────────────────────────────────────────────────────

def _get_by_tg_id_sync(tg_id: int) -> Optional[IdentityUser]:
    q = select(core_identity).where(core_identity.c.tg_id == tg_id).limit(1)
    with _get_engine().connect() as conn:
        row = conn.execute(q).fetchone()
    return _row_to_user(row) if row else None


def _get_by_notion_id_sync(notion_id: str) -> Optional[IdentityUser]:
    q = select(core_identity).where(core_identity.c.notion_id == notion_id).limit(1)
    with _get_engine().connect() as conn:
        row = conn.execute(q).fetchone()
    return _row_to_user(row) if row else None


def _get_all_sync() -> List[IdentityUser]:
    q = select(core_identity).order_by(core_identity.c.tg_id)
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_user(r) for r in rows]


def _upsert_sync(
    notion_id: str,
    tg_id: int,
    name: str,
    role: str,
    perm_nexus: bool,
    perm_arcana: bool,
    perm_finance: bool,
) -> IdentityUser:
    """Insert or update a row in core_identity by notion_id primary key."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(core_identity)
        .values(
            notion_id=notion_id,
            tg_id=tg_id,
            name=name,
            role=role,
            perm_nexus=perm_nexus,
            perm_arcana=perm_arcana,
            perm_finance=perm_finance,
        )
        .on_conflict_do_update(
            index_elements=["notion_id"],
            set_=dict(
                tg_id=tg_id,
                name=name,
                role=role,
                perm_nexus=perm_nexus,
                perm_arcana=perm_arcana,
                perm_finance=perm_finance,
            ),
        )
    )
    with _get_engine().begin() as conn:
        conn.execute(stmt)
    return IdentityUser(
        notion_id=notion_id, tg_id=tg_id, name=name, role=role,
        perm_nexus=perm_nexus, perm_arcana=perm_arcana, perm_finance=perm_finance,
    )


# ── Async repo ─────────────────────────────────────────────────────────────────

class PgIdentityRepo:
    async def get_by_tg_id(self, tg_id: int) -> Optional[IdentityUser]:
        return await asyncio.to_thread(_get_by_tg_id_sync, tg_id)

    async def get_by_notion_id(self, notion_id: str) -> Optional[IdentityUser]:
        return await asyncio.to_thread(_get_by_notion_id_sync, notion_id)

    async def get_all(self) -> List[IdentityUser]:
        return await asyncio.to_thread(_get_all_sync)

    async def upsert(
        self,
        notion_id: str,
        tg_id: int,
        name: str,
        role: str = "Тест",
        perm_nexus: bool = False,
        perm_arcana: bool = False,
        perm_finance: bool = False,
    ) -> IdentityUser:
        return await asyncio.to_thread(
            _upsert_sync, notion_id, tg_id, name, role, perm_nexus, perm_arcana, perm_finance
        )
