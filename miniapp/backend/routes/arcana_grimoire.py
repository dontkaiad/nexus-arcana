"""miniapp/backend/routes/arcana_grimoire.py — GET /api/arcana/grimoire, /{id}.

Гримуар в Notion — БАЗА ДАННЫХ (а не страница с блоками). Поля:
Название (title), Категория (select), Тема (multi_select), Текст (rich_text),
Источник (rich_text), Проверено (checkbox), 🪪 Пользователи (relation).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.notion_client import _grimoire_db_id, get_page, query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    first_emoji,
    multi_select_names,
    relation_ids_of,
    rich_text_plain,
    select_of,
    title_plain,
)

logger = logging.getLogger("miniapp.arcana.grimoire")

router = APIRouter()


def _preview(text: str, n: int = 120) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def _serialize_brief(page: dict) -> dict:
    themes = multi_select_names(page, "Тема")
    return {
        "id": page.get("id", ""),
        "name": title_plain(page, "Название"),
        "cat": select_of(page, "Категория") or None,
        "theme": first_emoji(themes[0]) if themes else None,
        "themes_count": len(themes),
        "preview": _preview(rich_text_plain(page, "Текст")),
    }


@router.get("/arcana/grimoire")
async def list_grimoire(
    tg_id: int = Depends(current_user_id),
    cat: Optional[str] = Query(None, description="фильтр по Категории"),
    q: Optional[str] = Query(None, description="contains по Название/Текст"),
) -> dict[str, Any]:
    db_id = _grimoire_db_id()
    if not db_id:
        return {"items": [], "categories": []}
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    conditions: list[dict] = []
    if cat:
        conditions.append({"property": "Категория", "select": {"equals": cat}})
    if user_notion_id:
        conditions.append({"property": "🪪 Пользователи",
                           "relation": {"contains": user_notion_id}})
    filters = None
    if conditions:
        filters = {"and": conditions} if len(conditions) > 1 else conditions[0]

    try:
        pages = await query_pages(
            db_id, filters=filters,
            sorts=[{"property": "Название", "direction": "ascending"}],
            page_size=200,
        )
    except Exception as e:
        logger.warning("grimoire query failed: %s", e)
        return {"items": [], "categories": []}

    items: list[dict] = []
    cats: set[str] = set()
    needle = q.lower().strip() if q else None
    for p in pages:
        brief = _serialize_brief(p)
        if brief["cat"]:
            cats.add(brief["cat"])
        if needle:
            hay = (brief["name"] or "").lower() + " " + (rich_text_plain(p, "Текст") or "").lower()
            if needle not in hay:
                continue
        items.append(brief)

    return {"items": items, "categories": sorted(cats)}


@router.get("/arcana/grimoire/{entry_id}")
async def grimoire_detail(
    entry_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        page = await get_page(entry_id)
    except Exception:
        raise HTTPException(status_code=404, detail="grimoire entry not found")
    if not page:
        raise HTTPException(status_code=404, detail="grimoire entry not found")

    owners = relation_ids_of(page, "🪪 Пользователи")
    if user_notion_id and user_notion_id not in owners:
        raise HTTPException(status_code=404, detail="grimoire entry not found")

    themes = multi_select_names(page, "Тема")
    return {
        "id": page.get("id", ""),
        "name": title_plain(page, "Название"),
        "cat": select_of(page, "Категория") or None,
        "themes": themes,
        "content": rich_text_plain(page, "Текст") or "",
        "source": rich_text_plain(page, "Источник") or None,
    }
