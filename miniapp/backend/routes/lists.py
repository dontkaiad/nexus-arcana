"""miniapp/backend/routes/lists.py — GET /api/lists."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    cat_from_notion,
    checkbox_value,
    date_start,
    number_value,
    rich_text,
    select_name,
    status_name,
    title_text,
)

logger = logging.getLogger("miniapp.lists")

router = APIRouter()

_TYPE_MAP = {
    "buy": "🛒 Покупки",
    "check": "📋 Чеклист",
    "inv": "📦 Инвентарь",
}


def _serialize(page: dict) -> dict:
    props = page.get("properties", {})
    status = status_name(props.get("Статус", {}))
    return {
        "id": page.get("id", ""),
        "name": title_text(props.get("Название", {})),
        "cat": cat_from_notion(select_name(props.get("Категория", {}))),
        "done": status == "Done",
        "status": status,
        "qty": number_value(props.get("Количество", {})),
        "price": number_value(props.get("Цена", {})),
        "note": rich_text(props.get("Заметка", {})) or None,
        "expires": date_start(props.get("Срок годности", {})) or None,
        "group": rich_text(props.get("Группа", {})) or None,
        "recurring": checkbox_value(props.get("Повторяющийся", {})),
    }


@router.get("/lists")
async def get_lists(
    tg_id: int = Depends(current_user_id),
    type: str = Query("buy", description="buy|check|inv"),
    q: Optional[str] = Query(None, description="case-insensitive contains по Название/Заметка"),
) -> dict[str, Any]:
    if type not in _TYPE_MAP:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(_TYPE_MAP)}")

    db_id = config.db_lists
    if not db_id:
        return {"type": type, "items": []}

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    # wave5.4: старые записи (созданные вручную в Notion или до появления поля "Бот")
    # могли остаться без заполненного "Бот". Разрешаем и Nexus, и пустое.
    conditions: list[dict] = [
        {"property": "Тип", "select": {"equals": _TYPE_MAP[type]}},
        {"or": [
            {"property": "Бот", "select": {"equals": BOT_NEXUS}},
            {"property": "Бот", "select": {"is_empty": True}},
        ]},
    ]
    if user_notion_id:
        conditions.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })

    # Сортировка: для инвентаря — по Сроку годности, иначе — по дате создания DESC
    sorts = (
        [{"property": "Срок годности", "direction": "ascending"}]
        if type == "inv"
        else [{"timestamp": "created_time", "direction": "descending"}]
    )

    try:
        pages = await query_pages(
            db_id, filters={"and": conditions}, sorts=sorts, page_size=200,
        )
    except Exception as e:
        logger.warning("lists query failed, retry without sort: %s", e)
        pages = await query_pages(db_id, filters={"and": conditions}, page_size=200)

    items = [_serialize(p) for p in pages]

    if q:
        needle = q.lower().strip()
        items = [
            i for i in items
            if needle in (i["name"] or "").lower()
            or needle in (i["note"] or "").lower()
        ]

    # Для 'inv' — записи без expires в конец
    if type == "inv":
        items.sort(key=lambda i: (i["expires"] is None, i["expires"] or ""))

    return {"type": type, "items": items}
