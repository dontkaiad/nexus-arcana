"""miniapp/backend/routes/categories.py — GET /api/categories?type=task|expense|income|list|memory."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from core.config import EXPENSE_CATEGORIES, INCOME_CATEGORIES
from core.list_manager import LIST_CATEGORIES
from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.categories")

router = APIRouter()


def _task_categories_sync() -> list:
    from nexus.repos.pg_tasks_repo import get_engine
    from nexus.repos.tasks_tables import task_category
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(task_category.c.code).order_by(task_category.c.id)
        ).fetchall()
    return [r[0] for r in rows]


@router.get("/categories")
async def get_categories(
    tg_id: int = Depends(current_user_id),
    type: str = Query("task", description="task|expense|income|list|memory"),
) -> dict[str, Any]:
    allowed = {"task", "expense", "income", "list", "memory"}
    if type not in allowed:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(allowed)}")

    if type == "task":
        import asyncio
        cats = await asyncio.to_thread(_task_categories_sync)
    elif type == "expense":
        cats = list(EXPENSE_CATEGORIES)
    elif type == "income":
        cats = list(INCOME_CATEGORIES)
    elif type == "list":
        cats = list(LIST_CATEGORIES)
    else:  # memory
        from miniapp.backend.routes.memory import CANONICAL_CATEGORIES
        cats = list(CANONICAL_CATEGORIES)

    return {"type": type, "categories": cats}
