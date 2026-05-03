"""miniapp/backend/routes/arcana_barter.py — бартер-чеклист.

Список пунктов 🗒️ Списки.Тип=📋 Чеклист, Категория=🔄 Бартер, Бот=🌒 Arcana.
Группа = название ритуала/расклада. Пользовательский фильтр обязателен.

Toggle Done использует существующий /api/lists/{item_id}/done.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from core.config import config
from core.notion_client import _with_user_filter, query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_ARCANA,
    rich_text_plain,
    select_name,
    status_name,
    title_text,
)

logger = logging.getLogger("miniapp.arcana.barter")

router = APIRouter()


def _serialize(page: dict) -> dict:
    props = page.get("properties", {})
    return {
        "id": page.get("id", ""),
        "name": title_text(props.get("Название", {})),
        "group": rich_text_plain(page, "Группа") or None,
        "status": status_name(props.get("Статус", {})),
        "done": status_name(props.get("Статус", {})) == "Done",
    }


async def _fetch(user_notion_id: str, only_open: bool = True) -> list[dict]:
    db_id = config.db_lists
    if not db_id:
        return []
    conditions: list[dict] = [
        {"property": "Тип", "select": {"equals": "📋 Чеклист"}},
        {"property": "Категория", "select": {"equals": "🔄 Бартер"}},
    ]
    if only_open:
        conditions.append({"property": "Статус", "status": {"does_not_equal": "Done"}})
        conditions.append({"property": "Статус", "status": {"does_not_equal": "Archived"}})
    base = {"and": conditions}
    filters = _with_user_filter(base, user_notion_id)
    try:
        pages = await query_pages(db_id, filters=filters, page_size=300)
    except Exception as e:
        logger.warning("barter fetch failed: %s", e)
        return []
    return [_serialize(p) for p in pages]


@router.get("/arcana/barter")
async def list_barter(
    only_open: bool = Query(True),
    group: Optional[str] = Query(None, description="точное совпадение по полю Группа"),
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    items = await _fetch(user_notion_id, only_open=only_open)
    if group:
        items = [i for i in items if (i.get("group") or "") == group]
    by_group: dict[str, list[dict]] = {}
    for it in items:
        g = it.get("group") or "—"
        by_group.setdefault(g, []).append(it)
    return {
        "items": items,
        "open_count": sum(1 for i in items if not i["done"]),
        "by_group": [{"group": g, "items": v} for g, v in by_group.items()],
    }
