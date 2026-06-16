"""miniapp/backend/routes/arcana_grimoire.py — GET /api/arcana/grimoire, /{id}.

Гримуар — чтение через PG (vertical slice: list + detail, no Notion calls).
Категории хранятся в GrimoireEntry.category как display-label с эмодзи.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from arcana.repos.grimoire_repo import GrimoireRepo
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import first_emoji

logger = logging.getLogger("miniapp.arcana.grimoire")

router = APIRouter()
_grimoire_repo = GrimoireRepo()

# Канонический порядок опций select-поля «Категория» (= PG display labels).
GRIMOIRE_CATEGORY_OPTIONS = [
    "📿 Заговор",
    "🧴 Рецепт",
    "✨ Комбинация",
    "📝 Заметка",
]


def _preview(text: str, n: int = 120) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


@router.get("/arcana/grimoire")
async def list_grimoire(
    tg_id: int = Depends(current_user_id),
    cat: Optional[str] = Query(None, description="фильтр по Категории"),
    q: Optional[str] = Query(None, description="contains по Название/Текст/Тема"),
) -> dict[str, Any]:
    def _empty_categories() -> list:
        return [{"name": name, "count": 0} for name in GRIMOIRE_CATEGORY_OPTIONS]

    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    try:
        entries = await _grimoire_repo.list_all(user_notion_id)
    except Exception as e:
        logger.warning("grimoire list_all failed: %s", e)
        return {"items": [], "categories": _empty_categories()}

    counts: dict = {name: 0 for name in GRIMOIRE_CATEGORY_OPTIONS}
    needle = q.lower().strip() if q else None
    items: list = []

    for entry in entries:
        if entry.category in counts:
            counts[entry.category] += 1
        elif entry.category:
            counts[entry.category] = counts.get(entry.category, 0) + 1

        if cat and entry.category != cat:
            continue
        if needle:
            # матчим Название + Текст + Тема (как раньше в Notion-пути)
            hay = " ".join([
                (entry.title or "").lower(),
                (entry.text or "").lower(),
                " ".join(entry.themes or []).lower(),
            ])
            if needle not in hay:
                continue

        theme0 = entry.themes[0] if entry.themes else None
        items.append({
            "id": entry.id,
            "name": entry.title,
            "cat": entry.category or None,
            "theme": first_emoji(theme0) if theme0 else None,
            "themes": entry.themes,
            "themes_count": len(entry.themes),
            "preview": _preview(entry.text),
            "source": entry.source or None,
            "verified": entry.verified,
        })

    ordered = [{"name": n, "count": counts[n]} for n in GRIMOIRE_CATEGORY_OPTIONS]
    extras = sorted(k for k in counts.keys() if k not in GRIMOIRE_CATEGORY_OPTIONS)
    ordered.extend({"name": k, "count": counts[k]} for k in extras)

    return {"items": items, "categories": ordered}


@router.get("/arcana/grimoire/{entry_id}")
async def grimoire_detail(
    entry_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        entry = await _grimoire_repo.find_by_id(entry_id, user_notion_id)
    except Exception:
        raise HTTPException(status_code=404, detail="grimoire entry not found")
    if not entry:
        raise HTTPException(status_code=404, detail="grimoire entry not found")

    return {
        "id": entry.id,
        "name": entry.title,
        "cat": entry.category or None,
        "themes": entry.themes,
        "content": entry.text or "",
        "source": entry.source or None,
        "verified": entry.verified,
    }
