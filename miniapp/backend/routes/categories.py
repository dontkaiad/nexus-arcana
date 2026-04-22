"""miniapp/backend/routes/categories.py — GET /api/categories?type=task|expense|income."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import select_name

logger = logging.getLogger("miniapp.categories")

router = APIRouter()


_DEFAULT_TASK_CATS = [
    "🏠 Дом", "💼 Работа", "💜 Люди", "🐾 Коты",
    "🛒 Покупки", "💰 Финансы", "🦋 Прочее",
]

_DEFAULT_EXPENSE_CATS = [
    "🍜 Продукты", "🏠 Жилье", "💳 Прочее", "🚬 Привычки",
    "💻 Подписки", "🚕 Транспорт", "🏥 Здоровье",
]

_DEFAULT_INCOME_CATS = [
    "💼 Зарплата", "💰 Фриланс", "🎁 Подарок", "🏦 Прочее",
]


async def _distinct_categories(db_id: str, user_notion_id: str,
                               category_field: str = "Категория") -> list[str]:
    """Fetch distinct non-empty category values from a Notion DB for this user."""
    filters: dict = {}
    if user_notion_id:
        filters = {
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        }
    try:
        pages = await query_pages(
            db_id, filters=filters or None, page_size=500,
        )
    except Exception as e:
        logger.warning("categories fetch failed: %s", e)
        return []
    seen: set[str] = set()
    out: list[str] = []
    for p in pages:
        props = p.get("properties", {})
        cat = select_name(props.get(category_field, {}))
        if cat and cat not in seen:
            seen.add(cat)
            out.append(cat)
    return out


@router.get("/categories")
async def get_categories(
    tg_id: int = Depends(current_user_id),
    type: str = Query("task", description="task|expense|income"),
) -> dict[str, Any]:
    if type not in {"task", "expense", "income"}:
        raise HTTPException(status_code=400, detail="type must be task|expense|income")

    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    if type == "task":
        db_id = config.nexus.db_tasks
        defaults = _DEFAULT_TASK_CATS
    elif type == "expense":
        db_id = config.nexus.db_finance
        defaults = _DEFAULT_EXPENSE_CATS
    else:  # income
        db_id = config.nexus.db_finance
        defaults = _DEFAULT_INCOME_CATS

    existing = await _distinct_categories(db_id, user_notion_id)
    # объединяем: существующие + дефолты которых нет в существующих
    merged = list(existing)
    for d in defaults:
        if d not in merged:
            merged.append(d)
    if not merged:
        merged = defaults
    return {"type": type, "categories": merged}
