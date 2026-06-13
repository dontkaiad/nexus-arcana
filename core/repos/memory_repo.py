"""core/repos/memory_repo.py — repository seam for 🧠 Память.

Seals all Notion API calls for the Memory domain so callers deal with
plain MemoryEntry objects and semantic operations, not Notion page dicts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from core import notion_client as _notion
from core.memory import _build_props, _get_db_id

logger = logging.getLogger("core.memory_repo")


@dataclass
class MemoryEntry:
    """Domain representation of one 🧠 Память page."""
    id: str
    fact: str
    category: str = ""
    связь: str = ""
    ключ: str = ""
    active: bool = True
    date: str = ""


class MemoryRepo:
    async def set_active(self, page_ids: List[str], active: bool) -> int:
        """Set Актуально on each page_id. Returns count of successful updates."""
        done = 0
        for pid in page_ids:
            try:
                await _notion.update_page(pid, {"Актуально": {"checkbox": active}})
                done += 1
            except Exception as e:
                logger.error("MemoryRepo.set_active %s: %s", pid[:8], e)
        return done

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
        """Save a pre-parsed memory fact. Returns page_id or None.

        upsert=True: if a page with the same ключ+category exists, update it
        instead of creating a duplicate. Used for limit/goal/debt facts.
        """
        db_id = _get_db_id()
        if not db_id:
            return None
        props = _build_props(fact, category, связь, ключ, bot_label, user_notion_id)
        if upsert and ключ and category:
            try:
                existing = await _notion.db_query(db_id, filter_obj={"and": [
                    {"property": "Ключ",      "rich_text": {"contains": ключ}},
                    {"property": "Категория", "select":    {"equals": category}},
                ]}, page_size=1)
                if existing:
                    await _notion.update_page(existing[0]["id"], props)
                    return existing[0]["id"]
            except Exception as e:
                logger.error("MemoryRepo.save_parsed upsert check: %s", e)
        try:
            return await _notion.page_create(db_id, props)
        except Exception as e:
            logger.error("MemoryRepo.save_parsed page_create: %s", e)
            return None


_repo = MemoryRepo()
