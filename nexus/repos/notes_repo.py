"""nexus/repos/notes_repo.py — domain repository for 💡 Заметки (Notes).

All Notion-specific structures (page dicts, prop helpers, direct Notion client
calls) are confined here. Callers receive plain Note dataclass instances.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from core import notion_client as _notion

logger = logging.getLogger("nexus.notes_repo")


@dataclass
class Note:
    """Domain representation of one 💡 Заметки page."""
    id: str
    title: str
    tags: List[str] = field(default_factory=list)
    date: str = ""
    category: str = ""


def _parse_note(page: dict) -> Note:
    props = page.get("properties", {})
    title_parts = props.get("Заголовок", {}).get("title", [])
    title = title_parts[0].get("plain_text", "") if title_parts else ""
    tags = [t["name"] for t in (props.get("Теги", {}).get("multi_select") or [])]
    date = ((props.get("Дата", {}).get("date") or {}).get("start", "") or "")[:10]
    category = (props.get("Категория", {}).get("select") or {}).get("name", "")
    return Note(id=page.get("id", ""), title=title, tags=tags, date=date, category=category)


class NotesRepo:
    async def add(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        date: Optional[str] = None,
        user_notion_id: str = "",
    ) -> Optional[str]:
        """Create a note page and return its page_id, or None on failure."""
        return await _notion.note_add(text=text, tags=tags, date=date, user_notion_id=user_notion_id)

    async def find_for_edit(self, db_id: str, hint: str) -> Optional[Note]:
        """Fetch one note for editing.

        hint='последняя' → most-recently-created note.
        Otherwise → first note whose title contains hint.
        Returns None if nothing found.
        """
        if hint == "последняя":
            pages = await _notion.db_query(
                db_id,
                sorts=[{"property": "Дата", "direction": "descending"}],
                page_size=1,
            )
        else:
            pages = await _notion.db_query(
                db_id,
                filter_obj={"property": "Заголовок", "title": {"contains": hint}},
                page_size=1,
            )
        if not pages:
            return None
        return _parse_note(pages[0])

    async def update_tags(self, page_id: str, tags: List[str]) -> None:
        """Replace the Теги multi_select on a note page."""
        try:
            await _notion.get_notion().pages.update(
                page_id=page_id,
                properties={"Теги": {"multi_select": [{"name": t} for t in tags]}},
            )
        except Exception as e:
            logger.error("update_tags %s failed: %s", page_id, e)

    async def archive(self, page_id: str) -> bool:
        """Archive (soft-delete) a note page. Returns True on success."""
        try:
            await _notion.get_notion().pages.update(page_id=page_id, archived=True)
            return True
        except Exception as e:
            logger.error("archive %s failed: %s", page_id, e)
            return False
