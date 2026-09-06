"""nexus/repos/notes_repo.py — repository seam for 💡 Заметки (Notes)."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("nexus.notes_repo")

from nexus.repos.pg_notes_repo import PgNotesRepo as _PgNotesRepo, Note  # noqa: F401 — re-export Note


class NotesRepo:
    def __init__(self) -> None:
        self._pg = _PgNotesRepo()

    async def add(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        date: Optional[str] = None,
        user_id: str = "",
    ) -> Optional[str]:
        return await self._pg.add(text=text, tags=tags, date=date, user_id=user_id)

    async def get_all_tags(self) -> List[str]:
        return await self._pg.get_all_tags()

    async def find_or_prepare_tag(self, raw: str) -> Tuple[str, bool]:
        return await self._pg.find_or_prepare_tag(raw)

    async def find_for_edit(self, hint: str, user_id: str = "") -> Optional[Note]:
        return await self._pg.find_for_edit(hint, user_id)

    async def update_tags(self, note_id: str, tags: List[str]) -> None:
        await self._pg.update_tags(note_id, tags)

    async def archive(self, note_id: str) -> bool:
        return await self._pg.archive(note_id)

    async def find_older_than_days(
        self, user_id: str = "", days: int = 7
    ) -> List[Note]:
        return await self._pg.find_older_than_days(user_id=user_id, days=days)

    async def list_recent(
        self, user_id: str = "", limit: int = 50
    ) -> List[Note]:
        return await self._pg.list_recent(user_id=user_id, limit=limit)

    async def search_by_tag(self, tag: str, user_id: str = "") -> List[Note]:
        return await self._pg.search_by_tag(tag, user_id)

    async def search_by_title(self, hint: str, user_id: str = "") -> List[Note]:
        return await self._pg.search_by_title(hint, user_id)
