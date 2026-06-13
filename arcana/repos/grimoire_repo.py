"""arcana/repos/grimoire_repo.py — domain repository for 📖 Гримуар.

Notion-specific structures (page dicts, prop helpers, select matching)
are fully contained here. Callers receive plain dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from core import notion_client as _notion


@dataclass
class GrimoireEntry:
    id: str
    title: str
    category: str       # "📿 Заговор" | "🧴 Рецепт" | "✨ Комбинация" | "📝 Заметка"
    themes: List[str]   # ["💰 Финансы", ...]
    verified: bool
    text: str
    source: str


@dataclass
class RitualSummary:
    name: str
    date: str           # "YYYY-MM-DD" or ""
    result_icon: str    # "✅" | "❌" | "〰️" | "⏳"


_RESULT_ICONS = {
    "✅ Сработало":    "✅",
    "❌ Не сработало": "❌",
    "〰️ Частично":     "〰️",
}


def _parse_grimoire_entry(page: dict) -> GrimoireEntry:
    props = page.get("properties", {})
    themes = [opt["name"] for opt in props.get("Тема", {}).get("multi_select", [])]
    return GrimoireEntry(
        id=page["id"],
        title=_notion._extract_text(props.get("Название", {})),
        category=_notion._extract_select(props.get("Категория", {})),
        themes=themes,
        verified=bool(props.get("Проверено", {}).get("checkbox", False)),
        text=_notion._extract_text(props.get("Текст", {})),
        source=_notion._extract_text(props.get("Источник", {})),
    )


def _parse_ritual_summary(page: dict) -> RitualSummary:
    props = page.get("properties", {})
    name = (
        _notion._extract_text(props.get("Тема", {}))
        or _notion._extract_text(props.get("Название", {}))
        or "—"
    )
    result = _notion._extract_select(props.get("Результат", {}))
    date_raw = (props.get("Дата") or {}).get("date") or {}
    return RitualSummary(
        name=name,
        date=(date_raw.get("start") or "")[:10],
        result_icon=_RESULT_ICONS.get(result, "⏳"),
    )


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
        return await _notion.grimoire_add(
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
        pages = await _notion.grimoire_list_by_category(category, user_notion_id)
        return [_parse_grimoire_entry(p) for p in pages]

    async def search(
        self,
        query: str = "",
        theme: Optional[str] = None,
        user_notion_id: str = "",
    ) -> List[GrimoireEntry]:
        pages = await _notion.grimoire_search(
            query=query,
            theme=theme,
            user_notion_id=user_notion_id,
        )
        return [_parse_grimoire_entry(p) for p in pages]

    async def rituals_list(
        self, user_notion_id: str = ""
    ) -> List[RitualSummary]:
        pages = await _notion.rituals_all(user_notion_id)
        return [_parse_ritual_summary(p) for p in pages]
