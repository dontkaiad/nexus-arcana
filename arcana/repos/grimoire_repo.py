"""arcana/repos/grimoire_repo.py — domain repository for 📖 Гримуар.

Pure PG — no Notion calls. Callers receive plain dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GrimoireEntry:
    id: str
    title: str
    category: str
    themes: List[str]
    verified: bool
    text: str
    source: str


@dataclass
class RitualSummary:
    name: str
    date: str
    result_icon: str


_RESULT_ICONS = {
    "positive": "✅",
    "negative": "❌",
    "partial":  "〰️",
    "unverified": "⏳",
}


def _pg_repo():
    from arcana.repos.pg_grimoire_repo import PgGrimoireRepo
    return PgGrimoireRepo()


def _pg_rituals():
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    return PgRitualsRepo()


class GrimoireRepo:
    async def add(
        self,
        title: str,
        category: str,
        themes: Optional[List[str]] = None,
        text: str = "",
        source: str = "",
        user_notion_id: str = "",
    ) -> Optional[str]:
        return await _pg_repo().add(
            title=title,
            category=category,
            themes=themes,
            text=text,
            source=source,
            user_notion_id=user_notion_id,
        )

    async def list_by_category(
        self, category: str, user_notion_id: str = ""
    ) -> List[GrimoireEntry]:
        return await _pg_repo().list_by_category(category, user_notion_id)

    async def search(
        self,
        query: str = "",
        theme: Optional[str] = None,
        user_notion_id: str = "",
    ) -> List[GrimoireEntry]:
        return await _pg_repo().search(query=query, theme=theme, user_notion_id=user_notion_id)

    async def rituals_list(
        self, user_notion_id: str = ""
    ) -> List[RitualSummary]:
        rituals = await _pg_rituals().list_all(user_notion_id=user_notion_id)
        result = []
        for r in rituals:
            d = r.date
            date_str = d.strftime("%Y-%m-%d") if d else ""
            icon = _RESULT_ICONS.get(r.result or "unverified", "⏳")
            result.append(RitualSummary(
                name=r.name or "—",
                date=date_str,
                result_icon=icon,
            ))
        return result
