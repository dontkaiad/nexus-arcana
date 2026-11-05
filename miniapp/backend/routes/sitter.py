"""miniapp/backend/routes/sitter.py — списки «для ситтера».

• GET  /api/sitter/lists          — служебное чтение по отдельному Bearer-токену (sitter_lists_gate); только пункты
                                    с пометкой for_sitter, без id, цен и остальных данных Nexus (память, прочие списки).
• POST /api/lists/{id}/sitter     — владелец (обычный вход) ставит / снимает пометку у своего пункта.
"""
from __future__ import annotations

import asyncio
import logging
from itertools import groupby
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.repos import pg_sitter_lists_repo as repo
from core.user_manager import get_user_id
from miniapp.backend.auth import current_user_id
from miniapp.backend.sitter_lists_auth import sitter_lists_gate

logger = logging.getLogger("miniapp.sitter")

router = APIRouter()


@router.get("/sitter/lists")
async def sitter_lists(owner_tg_id: int = Depends(sitter_lists_gate)) -> Dict[str, Any]:
    user_id = await get_user_id(owner_tg_id)
    items = await asyncio.to_thread(repo.list_for_sitter, user_id or "")
    lists = [{"name": g or "", "items": [{k: v for k, v in i.items() if k != "group"} for i in grp]}
             for g, grp in groupby(items, key=lambda i: i["group"])]
    return {"lists": lists, "count": len(items)}


class SitterFlagBody(BaseModel):
    for_sitter: bool


@router.post("/lists/{item_id}/sitter")
async def set_sitter_flag(item_id: int, body: SitterFlagBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    if not user_id or not await asyncio.to_thread(repo.set_flag, user_id, item_id, body.for_sitter):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "for_sitter": body.for_sitter}
