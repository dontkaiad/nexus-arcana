"""miniapp/backend/routes/cats.py — /api/cats: домен «Коты» (карточка кота + его факты из Памяти).

Раздел строится от таблицы `cats`, а не от категории: карточка = cats + memories.cat_id. Факты категории
«🐾 Коты» без cat_id — отдельный блок «Общее про котов». Доступ как у «Людей»: только владелец.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from core.cats import group_facts
from core.repos import pg_cats_repo as repo
from core.user_manager import get_user_id
from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.cats")

router = APIRouter()


async def _owner(tg_id: int) -> str:
    user_id = await get_user_id(tg_id)
    if not user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return user_id


def _cat_out(c: Dict[str, Any], facts_count: int = 0) -> Dict[str, Any]:
    return {
        "id": c["id"],
        "name": c["name"],
        "aliases": list(c.get("aliases") or []),
        "photo_url": c.get("photo_url"),           # None → фронт рисует заглушку и кнопку «Загрузить фото»
        "status": c["status"],
        "facts_count": facts_count,
    }


@router.get("/cats")
async def list_cats(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    rows: List[Dict[str, Any]] = await asyncio.to_thread(repo.list_cats, user_id)
    counts = await asyncio.to_thread(repo.facts_count, user_id, [r["id"] for r in rows])
    general = await asyncio.to_thread(repo.list_general_facts, user_id)
    return {"total": len(rows), "cats": [_cat_out(r, counts.get(r["id"], 0)) for r in rows],
            "general_count": len(general)}


@router.get("/cats/general")
async def general_facts(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """«Общее про котов». Объявлен ДО /cats/{cat_id}, иначе «general» уйдёт в int-параметр."""
    user_id = await _owner(tg_id)
    facts = await asyncio.to_thread(repo.list_general_facts, user_id)
    return {"total": len(facts), "facts": group_facts(facts, strip_name=False)}


@router.get("/cats/{cat_id}")
async def cat_card(cat_id: int, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = await _owner(tg_id)
    c = await asyncio.to_thread(repo.get_cat, user_id, cat_id)
    if not c or c["status"] == "rejected":
        raise HTTPException(status_code=404, detail="cat not found")
    facts = await asyncio.to_thread(repo.list_facts, user_id, cat_id)
    return {**_cat_out(c, len(facts)), "facts": group_facts(facts, c["name"], c.get("aliases"))}
