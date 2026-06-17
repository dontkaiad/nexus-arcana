"""miniapp/backend/routes/arcana_barter.py — бартер-чеклист.

Список пунктов arcana_inventory с category='🔄 Бартер'.
Группа = название ритуала/расклада. Пользовательский фильтр обязателен.

Toggle Done использует существующий /api/lists/{item_id}/done.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from core.repos.pg_nexus_lists_repo import (
    BARTER_CATEGORY,
    InventoryItem,
    PgArcanaInventoryRepo,
    _notion_status,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.arcana.barter")

router = APIRouter()

_inv_repo = PgArcanaInventoryRepo()


def _serialize_item(item: InventoryItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "group": item.group_name or None,
        # PG хранит "done"/"not_started"; конвертируем обратно в Notion-формат для фронта
        "status": _notion_status(item.status),
        "done": item.status == "done",
    }


async def _fetch(user_notion_id: str, only_open: bool = True) -> list:
    try:
        if only_open:
            items = await _inv_repo.get_open_barter(user_notion_id)
        else:
            items = await _inv_repo.get_list(
                category=BARTER_CATEGORY, user_notion_id=user_notion_id
            )
    except Exception as e:
        logger.warning("barter fetch failed: %s", e)
        return []
    return [_serialize_item(it) for it in items]


@router.get("/arcana/barter")
async def list_barter(
    only_open: bool = Query(True),
    group: Optional[str] = Query(None, description="точное совпадение по полю Группа"),
    tg_id: int = Depends(current_user_id),
) -> dict:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    items = await _fetch(user_notion_id, only_open=only_open)
    if group:
        items = [i for i in items if (i.get("group") or "") == group]
    by_group: dict = {}
    for it in items:
        g = it.get("group") or "—"
        by_group.setdefault(g, []).append(it)
    return {
        "items": items,
        "open_count": sum(1 for i in items if not i["done"]),
        "by_group": [{"group": g, "items": v} for g, v in by_group.items()],
    }
