"""miniapp/backend/routes/sitter.py — списки «для ситтера».

• GET  /api/sitter/lists          — служебное чтение по отдельному Bearer-токену (sitter_lists_gate): помеченные покупки и
                                    чеклисты (`lists`) + видимый инвентарь по категориям (`inventory`); условие видимости одно
                                    (repo.sitter_visible_clause), без id, цен и остальных данных Nexus (память, прочие списки).
• POST /api/sitter/ask            — поиск ситтера без LLM по тем же источникам (+ факты про котов), тем же читающим токеном.
• GET  /api/lists/sitter-preview  — владелец видит ровно то же, что ситтер, + id для переключателей.
• POST /api/lists/{id}/sitter     — владелец ставит / снимает пометку «для ситтера» (покупки, чеклист).
• POST /api/lists/{id}/sitter-hidden — владелец скрывает / открывает пункт инвентаря.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import sitter_search
from core.repos import pg_sitter_lists_repo as repo
from core.user_manager import get_user_id
from miniapp.backend.auth import current_user_id
from miniapp.backend.sitter_lists_auth import sitter_lists_gate

logger = logging.getLogger("miniapp.sitter")

router = APIRouter()


@router.get("/sitter/lists")
async def sitter_lists(owner_tg_id: int = Depends(sitter_lists_gate)) -> Dict[str, Any]:
    user_id = (await get_user_id(owner_tg_id)) or ""
    return await asyncio.to_thread(_payload, user_id, False)


def _payload(user_id: str, with_ids: bool) -> Dict[str, Any]:
    items = repo.list_for_sitter(user_id, with_ids)
    inv = repo.list_inventory(user_id, with_ids)
    return {"lists": repo.group_by(items, "group", "name"), "count": len(items),
            "inventory": repo.group_by(inv, "category", "category"), "inventory_count": len(inv)}


_budget = sitter_search.QueryBudget()


class AskBody(BaseModel):
    q: str = Field(min_length=1, max_length=2000)
    offset: int = Field(default=0, ge=0, le=sitter_search.MAX_OFFSET)     # «Показать ещё»: с какого фрагмента отдавать


@router.post("/sitter/ask")
async def sitter_ask(body: AskBody, owner_tg_id: int = Depends(sitter_lists_gate)) -> Dict[str, Any]:
    """Поиск ситтера без LLM: факты про котов, «Общее про котов» и видимые ситтеру пункты списков (см. core/sitter_search.py)."""
    q = sitter_search.clean_query(body.q)
    if not q:
        raise HTTPException(status_code=422, detail="empty query")
    wait = _budget.take()
    if wait:
        raise HTTPException(status_code=429, detail="too many questions", headers={"Retry-After": str(wait)})
    user_id = (await get_user_id(owner_tg_id)) or ""
    return await sitter_search.ask(user_id, q, body.offset)


@router.get("/lists/sitter-preview")
async def sitter_preview(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Владельцу: ровно то, что получит ситтер (тем же кодом), плюс id для переключателей."""
    user_id = (await get_user_id(tg_id)) or ""
    return await asyncio.to_thread(_payload, user_id, True)


class SitterFlagBody(BaseModel):
    for_sitter: bool


class SitterGroupBody(BaseModel):
    group: str
    for_sitter: bool


@router.post("/lists/sitter-group")
async def set_sitter_group_flag(body: SitterGroupBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    """Пометить / снять весь чеклист (все его активные пункты) для ситтера."""
    group = (body.group or "").strip()
    if not group:
        raise HTTPException(status_code=400, detail="group required")
    user_id = (await get_user_id(tg_id)) or ""
    n = await asyncio.to_thread(repo.set_group_flag, user_id, group, body.for_sitter) if user_id else 0
    if not n:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "for_sitter": body.for_sitter, "items": n}


class SitterHiddenBody(BaseModel):
    hidden: bool


@router.post("/lists/{item_id}/sitter-hidden")
async def set_sitter_hidden(item_id: int, body: SitterHiddenBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    if not user_id or not await asyncio.to_thread(repo.set_hidden, user_id, item_id, body.hidden):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "hidden": body.hidden}


@router.post("/lists/{item_id}/sitter")
async def set_sitter_flag(item_id: int, body: SitterFlagBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    if not user_id or not await asyncio.to_thread(repo.set_flag, user_id, item_id, body.for_sitter):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "for_sitter": body.for_sitter}
