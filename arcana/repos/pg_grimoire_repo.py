"""arcana/repos/pg_grimoire_repo.py — PostgreSQL adapter for 📖 Гримуар."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, List

from sqlalchemy import select, or_

from arcana.repos.grimoire_repo import GrimoireEntry
from arcana.repos.grimoire_tables import grimoire_entries, grimoire_category
from core.db import get_engine

logger = logging.getLogger("arcana.pg_grimoire")

_CATEGORY_TO_CODE = {
    "📿 заговор":    "spell",
    "🧴 рецепт":     "recipe",
    "✨ комбинация": "combo",
    "📝 заметка":    "note",
    # bare
    "заговор":    "spell",
    "рецепт":     "recipe",
    "комбинация": "combo",
    "заметка":    "note",
    # pass-through
    "spell":   "spell",
    "recipe":  "recipe",
    "combo":   "combo",
    "note":    "note",
}

# Maps PG code → display label with emoji (for GrimoireEntry.category)
_CODE_TO_DISPLAY = {
    "spell":  "📿 Заговор",
    "recipe": "🧴 Рецепт",
    "combo":  "✨ Комбинация",
    "note":   "📝 Заметка",
}


def _code_for(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return _CATEGORY_TO_CODE.get(raw.lower().strip()) or _CATEGORY_TO_CODE.get(raw.strip())


def _resolve(conn, code: Optional[str]) -> Optional[int]:
    if not code:
        return None
    row = conn.execute(
        select(grimoire_category.c.id).where(grimoire_category.c.code == code)
    ).fetchone()
    return row[0] if row else None


def _row_to_entry(row) -> GrimoireEntry:
    themes_str = row.themes or ""
    themes = [t.strip() for t in themes_str.split(",") if t.strip()] if themes_str else []
    cat_code = row.cat_code or "note"
    return GrimoireEntry(
        id=str(row.id),
        title=row.title or "",
        category=_CODE_TO_DISPLAY.get(cat_code, cat_code),
        themes=themes,
        verified=bool(row.verified),
        text=row.text or "",
        source=row.source or "",
    )


def _select_grimoire():
    gc = grimoire_category.alias("gc")
    return (
        select(
            grimoire_entries.c.id,
            grimoire_entries.c.title,
            grimoire_entries.c.themes,
            grimoire_entries.c.verified,
            grimoire_entries.c.text,
            grimoire_entries.c.source,
            gc.c.code.label("cat_code"),
        )
        .outerjoin(gc, grimoire_entries.c.category_id == gc.c.id)
        .order_by(grimoire_entries.c.created_at.desc())
    )


class PgGrimoireRepo:

    def _create_sync(
        self,
        title: str,
        category: str,
        themes: Optional[List[str]],
        text: str,
        source: str,
        user_notion_id: str,
    ) -> Optional[str]:
        cat_code = _code_for(category)
        themes_str = ", ".join(themes) if themes else None
        with get_engine().begin() as conn:
            cat_id = _resolve(conn, cat_code)
            row = conn.execute(
                grimoire_entries.insert().values(
                    title=title,
                    category_id=cat_id,
                    themes=themes_str,
                    verified=False,
                    text=text or None,
                    source=source or None,
                    user_notion_id=user_notion_id or None,
                ).returning(grimoire_entries.c.id)
            ).fetchone()
        return str(row[0]) if row else None

    def _list_by_category_sync(
        self, category: str, user_notion_id: str
    ) -> List[GrimoireEntry]:
        cat_code = _code_for(category)
        if not cat_code:
            return []
        stmt = (
            _select_grimoire()
            .where(grimoire_category.c.code == cat_code)
        )
        if user_notion_id:
            stmt = stmt.where(grimoire_entries.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_entry(r) for r in rows]

    def _search_sync(
        self,
        query: str,
        theme: Optional[str],
        user_notion_id: str,
    ) -> List[GrimoireEntry]:
        stmt = _select_grimoire()
        if user_notion_id:
            stmt = stmt.where(grimoire_entries.c.user_notion_id == user_notion_id)
        if query:
            stmt = stmt.where(
                or_(
                    grimoire_entries.c.title.ilike(f"%{query}%"),
                    grimoire_entries.c.text.ilike(f"%{query}%"),
                )
            )
        if theme:
            stmt = stmt.where(grimoire_entries.c.themes.ilike(f"%{theme}%"))
        stmt = stmt.limit(50)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_entry(r) for r in rows]

    # ── Public async interface ────────────────────────────────────────────────

    async def add(
        self,
        title: str,
        category: str,
        themes: Optional[List[str]] = None,
        text: str = "",
        source: str = "",
        user_notion_id: str = "",
    ) -> Optional[str]:
        return await asyncio.to_thread(
            self._create_sync, title, category, themes, text, source, user_notion_id
        )

    async def list_by_category(
        self, category: str, user_notion_id: str = ""
    ) -> List[GrimoireEntry]:
        return await asyncio.to_thread(
            self._list_by_category_sync, category, user_notion_id
        )

    async def search(
        self,
        query: str = "",
        theme: Optional[str] = None,
        user_notion_id: str = "",
    ) -> List[GrimoireEntry]:
        return await asyncio.to_thread(
            self._search_sync, query, theme, user_notion_id
        )
