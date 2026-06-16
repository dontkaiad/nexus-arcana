"""core/repos/pg_memory_repo.py — PG implementation for 🧠 Память (ADR-0005)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, text, or_
from sqlalchemy.engine import Engine

from core.repos.memories_table import memories

logger = logging.getLogger("core.pg_memory_repo")

_engine: Optional[Engine] = None

_BOT_TO_SCOPE: Dict[str, str] = {
    "☀️ Nexus": "nexus",
    "🌒 Arcana": "arcana",
}
_SCOPE_TO_BOT: Dict[str, str] = {v: k for k, v in _BOT_TO_SCOPE.items()}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from arcana.repos.pg_sessions_repo import get_engine as _arc_engine
        _engine = _arc_engine()
    return _engine


def bot_to_scope(bot_label: str) -> str:
    return _BOT_TO_SCOPE.get(bot_label, "global")


def scope_to_bot(scope: str) -> str:
    return _SCOPE_TO_BOT.get(scope, "")


# ── Domain object ──────────────────────────────────────────────────────────────

@dataclass
class Memory:
    """Domain representation of one 🧠 Память row."""
    id: str
    fact: str
    key: str = ""
    value: str = ""
    category: str = ""
    scope: str = "global"    # "global" | "nexus" | "arcana"
    source: str = "manual"
    related_to: str = ""
    is_current: bool = True
    is_archived: bool = False
    user_notion_id: str = ""
    date: str = ""           # created_at[:10]


# ── Row → domain object ───────────────────────────────────────────────────────

def _row_to_memory(row) -> Memory:
    created = getattr(row, "created_at", None)
    date_str = created.date().isoformat() if created else ""
    return Memory(
        id=str(row.id),
        fact=row.fact_text or "",
        key=row.key_name or "",
        value=row.value_text or "",
        category=row.category or "",
        scope=row.scope or "global",
        source=row.source or "manual",
        related_to=row.related_to or "",
        is_current=bool(row.is_current),
        is_archived=bool(row.is_archived),
        user_notion_id=row.user_notion_id or "",
        date=date_str,
    )


# ── Sync helpers ──────────────────────────────────────────────────────────────

def _add_sync(
    fact: str,
    key: str,
    category: str,
    scope: str,
    related_to: str,
    source: str,
    user_notion_id: str,
    notion_id: Optional[str] = None,
) -> str:
    with get_engine().begin() as conn:
        result = conn.execute(
            memories.insert().values(
                notion_id=notion_id,
                fact_text=fact,
                key_name=key or "",
                category=category or "",
                scope=scope or "global",
                source=source or "manual",
                related_to=related_to or "",
                is_current=True,
                is_archived=False,
                user_notion_id=user_notion_id or "",
            ).returning(memories.c.id)
        )
        return str(result.fetchone()[0])


def _upsert_sync(
    fact: str,
    key: str,
    category: str,
    scope: str,
    related_to: str,
    source: str,
    user_notion_id: str,
) -> Tuple[str, bool]:
    """Find existing by key+category, update; else create. Returns (id, was_updated)."""
    if key and category:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(memories.c.id)
                .where(memories.c.key_name == key)
                .where(memories.c.category == category)
                .where(memories.c.is_archived == False)  # noqa: E712
                .order_by(memories.c.created_at.desc())
                .limit(1)
            ).fetchone()
        if row:
            mem_id = row[0]
            with get_engine().begin() as conn:
                conn.execute(
                    memories.update()
                    .where(memories.c.id == mem_id)
                    .values(
                        fact_text=fact,
                        key_name=key,
                        category=category,
                        scope=scope or "global",
                        source=source or "manual",
                        related_to=related_to or "",
                        is_current=True,
                        user_notion_id=user_notion_id or "",
                        updated_at=text("now()"),
                    )
                )
            return str(mem_id), True
    mem_id = _add_sync(fact, key, category, scope, related_to, source, user_notion_id)
    return mem_id, False


def _set_current_sync(memory_ids: List[str], is_current: bool) -> int:
    if not memory_ids:
        return 0
    ids_int = []
    for mid in memory_ids:
        try:
            ids_int.append(int(mid))
        except (ValueError, TypeError):
            pass
    if not ids_int:
        return 0
    with get_engine().begin() as conn:
        result = conn.execute(
            memories.update()
            .where(memories.c.id.in_(ids_int))
            .values(is_current=is_current, updated_at=text("now()"))
        )
    return result.rowcount


def _archive_sync(memory_id: str) -> bool:
    try:
        mid = int(memory_id)
        with get_engine().begin() as conn:
            conn.execute(
                memories.update()
                .where(memories.c.id == mid)
                .values(is_archived=True, updated_at=text("now()"))
            )
        return True
    except Exception as e:
        logger.error("archive %s failed: %s", memory_id, e)
        return False


def _base_active_q():
    return (
        select(memories)
        .where(memories.c.is_current == True)   # noqa: E712
        .where(memories.c.is_archived == False)  # noqa: E712
    )


def _search_sync(
    terms: List[str],
    scope: str = "",
    user_notion_id: str = "",
    page_size: int = 10,
) -> List[Memory]:
    if not terms:
        return []
    conditions = []
    for term in terms:
        like = f"%{term}%"
        conditions.append(memories.c.fact_text.ilike(like))
        conditions.append(memories.c.key_name.ilike(like))
        conditions.append(memories.c.related_to.ilike(like))
    q = _base_active_q().where(or_(*conditions))
    if scope and scope != "global":
        q = q.where(or_(memories.c.scope == scope, memories.c.scope == "global"))
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_by_category_sync(
    category: str,
    is_current: bool = True,
    scope: str = "",
    user_notion_id: str = "",
    page_size: int = 100,
) -> List[Memory]:
    q = (
        select(memories)
        .where(memories.c.is_archived == False)  # noqa: E712
        .where(memories.c.is_current == is_current)
    )
    if category:
        q = q.where(memories.c.category == category)
    if scope and scope != "global":
        q = q.where(or_(memories.c.scope == scope, memories.c.scope == "global"))
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_by_key_sync(
    key: str,
    category: str = "",
    user_notion_id: str = "",
    page_size: int = 5,
) -> List[Memory]:
    q = _base_active_q()
    if key:
        q = q.where(
            or_(
                memories.c.key_name.ilike(f"%{key}%"),
                memories.c.fact_text.ilike(f"%{key}%"),
            )
        )
    if category:
        q = q.where(memories.c.category == category)
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_by_key_prefixes_sync(
    prefixes: List[str],
    user_notion_id: str = "",
) -> List[Memory]:
    """Find memories whose key_name starts with any of the given prefixes."""
    if not prefixes:
        return []
    conditions = [memories.c.key_name.ilike(f"{p}%") for p in prefixes]
    q = (
        _base_active_q()
        .where(or_(*conditions))
    )
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(500)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_recent_sync(
    is_current: Optional[bool] = None,
    scope: str = "",
    user_notion_id: str = "",
    page_size: int = 10,
) -> List[Memory]:
    q = select(memories).where(memories.c.is_archived == False)  # noqa: E712
    if is_current is not None:
        q = q.where(memories.c.is_current == is_current)
    if scope and scope != "global":
        q = q.where(or_(memories.c.scope == scope, memories.c.scope == "global"))
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


# ── Public async API ───────────────────────────────────────────────────────────

class PgMemoryRepo:
    async def add(
        self,
        fact: str,
        key: str = "",
        category: str = "",
        scope: str = "global",
        related_to: str = "",
        source: str = "manual",
        user_notion_id: str = "",
        notion_id: Optional[str] = None,
    ) -> str:
        return await asyncio.to_thread(
            _add_sync, fact, key, category, scope, related_to, source, user_notion_id, notion_id
        )

    async def upsert(
        self,
        fact: str,
        key: str = "",
        category: str = "",
        scope: str = "global",
        related_to: str = "",
        source: str = "manual",
        user_notion_id: str = "",
    ) -> Tuple[str, bool]:
        return await asyncio.to_thread(
            _upsert_sync, fact, key, category, scope, related_to, source, user_notion_id
        )

    async def set_current(self, memory_ids: List[str], is_current: bool) -> int:
        return await asyncio.to_thread(_set_current_sync, memory_ids, is_current)

    async def archive(self, memory_id: str) -> bool:
        return await asyncio.to_thread(_archive_sync, memory_id)

    async def search(
        self,
        terms: List[str],
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 10,
    ) -> List[Memory]:
        return await asyncio.to_thread(_search_sync, terms, scope, user_notion_id, page_size)

    async def find_by_category(
        self,
        category: str,
        is_current: bool = True,
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 100,
    ) -> List[Memory]:
        return await asyncio.to_thread(
            _find_by_category_sync, category, is_current, scope, user_notion_id, page_size
        )

    async def find_by_key(
        self,
        key: str,
        category: str = "",
        user_notion_id: str = "",
        page_size: int = 5,
    ) -> List[Memory]:
        return await asyncio.to_thread(_find_by_key_sync, key, category, user_notion_id, page_size)

    async def find_by_key_prefixes(
        self,
        prefixes: List[str],
        user_notion_id: str = "",
    ) -> List[Memory]:
        return await asyncio.to_thread(_find_by_key_prefixes_sync, prefixes, user_notion_id)

    async def find_recent(
        self,
        is_current: Optional[bool] = None,
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 10,
    ) -> List[Memory]:
        return await asyncio.to_thread(
            _find_recent_sync, is_current, scope, user_notion_id, page_size
        )
