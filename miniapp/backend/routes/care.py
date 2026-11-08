"""miniapp/backend/routes/care.py — уход за котами через ситтера.

• GET  /api/care/schedule     — расписание для ситтера (читающий Bearer-токен ситтера, тот же гейт, что у /api/sitter/lists):
                                задачи с for_sitter и их текущий цикл; вне периода ухода задач нет.
• GET/PUT/DELETE /api/care/period — владелец задаёт период, в который ситтер активен.
• POST /api/tasks/{id}/sitter — владелец включает / выключает задачу в расписании ситтера.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import care
from core.location import get_user_tz
from core.user_manager import get_user_id
from miniapp.backend.auth import current_user_id
from miniapp.backend.sitter_lists_auth import sitter_lists_gate

logger = logging.getLogger("miniapp.care")

router = APIRouter()


@router.get("/care/schedule")
async def care_schedule(owner_tg_id: int = Depends(sitter_lists_gate)) -> Dict[str, Any]:
    user_id = (await get_user_id(owner_tg_id)) or ""
    tz = await get_user_tz(owner_tg_id)
    return await asyncio.to_thread(care.schedule, user_id, tz)


class PeriodBody(BaseModel):
    starts_on: date
    ends_on: date


def _period_json(p: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {"period": {"starts_on": p["starts_on"].isoformat(), "ends_on": p["ends_on"].isoformat(), "source": p.get("source", "manual")} if p else None}


@router.get("/care/period")
async def get_period(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    tz = await get_user_tz(tg_id)
    out = _period_json(await asyncio.to_thread(care.get_period, user_id))
    out["active"] = await asyncio.to_thread(care.is_active, user_id, tz) if user_id else False
    return out


@router.put("/care/period")
async def put_period(body: PeriodBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    if not user_id:
        raise HTTPException(status_code=404, detail="not found")
    try:
        await asyncio.to_thread(care.set_period, user_id, body.starts_on, body.ends_on)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **_period_json({"starts_on": body.starts_on, "ends_on": body.ends_on, "source": "manual"})}


@router.delete("/care/period")
async def delete_period(tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    if not user_id or not await asyncio.to_thread(care.clear_period, user_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


class TaskSitterBody(BaseModel):
    for_sitter: bool


@router.post("/tasks/{task_id}/sitter")
async def task_sitter(task_id: int, body: TaskSitterBody, tg_id: int = Depends(current_user_id)) -> Dict[str, Any]:
    user_id = (await get_user_id(tg_id)) or ""
    if not user_id or not await asyncio.to_thread(care.set_task_flag, user_id, task_id, body.for_sitter):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "for_sitter": body.for_sitter}
