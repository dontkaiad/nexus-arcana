"""core/repos/memory_repo.py — repository seam for 🧠 Память.

Delegates all storage to PgMemoryRepo. Public API kept stable
so callers (memory.py, finance.py handlers) need no signature changes.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from core.repos.pg_memory_repo import (
    PgMemoryRepo as _PgMemoryRepo,
    Memory,  # noqa: F401 — re-export
    bot_to_scope,
)

logger = logging.getLogger("core.memory_repo")


class MemoryRepo:
    def __init__(self) -> None:
        self._pg = _PgMemoryRepo()

    # ── Activation/deactivation ───────────────────────────────────────────────

    async def set_active(self, page_ids: List[str], active: bool) -> int:
        """Set is_current on each memory_id. Returns count of updated rows."""
        return await self._pg.set_current(page_ids, active)

    # ── Write ─────────────────────────────────────────────────────────────────

    async def save_parsed(
        self,
        fact: str,
        category: str,
        связь: str,
        ключ: str,
        bot_label: str,
        user_notion_id: str = "",
        upsert: bool = False,
    ) -> Optional[str]:
        """Save a pre-parsed memory fact. Returns memory_id or None.

        upsert=True: if a row with the same ключ+category exists, update it.
        Used for limit/goal/debt facts.
        """
        scope = bot_to_scope(bot_label)
        try:
            if upsert:
                pid, _ = await self._pg.upsert(
                    fact, ключ, category, scope, связь, "manual", user_notion_id
                )
                return pid
            return await self._pg.add(
                fact, ключ, category, scope, связь, "manual", user_notion_id
            )
        except Exception as e:
            logger.error("MemoryRepo.save_parsed: %s", e)
            return None

    async def add(
        self,
        fact: str,
        key: str,
        category: str,
        scope: str,
        related_to: str,
        source: str,
        user_notion_id: str,
    ) -> Optional[str]:
        """Lower-level add (used by save_memory directly)."""
        try:
            return await self._pg.add(fact, key, category, scope, related_to, source, user_notion_id)
        except Exception as e:
            logger.error("MemoryRepo.add: %s", e)
            return None

    async def upsert(
        self,
        fact: str,
        key: str,
        category: str,
        scope: str,
        related_to: str,
        source: str,
        user_notion_id: str,
    ) -> Tuple[Optional[str], bool]:
        """Upsert (find by key+category → update; else create). Returns (id, was_updated)."""
        try:
            return await self._pg.upsert(fact, key, category, scope, related_to, source, user_notion_id)
        except Exception as e:
            logger.error("MemoryRepo.upsert: %s", e)
            return None, False

    async def archive(self, memory_id: str) -> bool:
        return await self._pg.archive(memory_id)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def search(
        self,
        terms: List[str],
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 10,
    ) -> List[Memory]:
        return await self._pg.search(terms, scope, user_notion_id, page_size)

    async def find_by_category(
        self,
        category: str,
        is_current: bool = True,
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 100,
    ) -> List[Memory]:
        return await self._pg.find_by_category(category, is_current, scope, user_notion_id, page_size)

    async def find_by_key(
        self,
        key: str,
        category: str = "",
        user_notion_id: str = "",
        page_size: int = 5,
    ) -> List[Memory]:
        return await self._pg.find_by_key(key, category, user_notion_id, page_size)

    async def find_by_key_prefixes(
        self,
        prefixes: List[str],
        user_notion_id: str = "",
    ) -> List[Memory]:
        return await self._pg.find_by_key_prefixes(prefixes, user_notion_id)

    async def find_recent(
        self,
        is_current: Optional[bool] = None,
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 10,
    ) -> List[Memory]:
        return await self._pg.find_recent(is_current, scope, user_notion_id, page_size)


_repo = MemoryRepo()
