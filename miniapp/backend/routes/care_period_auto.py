"""miniapp/backend/routes/care_period_auto.py — период ухода из дат доступа ситтеров (cats присылает отдельным токеном).

• PUT /api/care/period/auto {starts_on, ends_on} | {starts_on: null, ends_on: null}
    даты — задать период (source='auto'), null/null — «периода нет» (снять автоматический).
    Ручной период владельца (/care_period, Mini App) — переопределение: он НЕ перезаписывается и не снимается, ответ 200 с
    applied=false, reason='manual_override'. Идемпотентно: повтор тех же дат — changed=false.
Токен открывает только этот маршрут (miniapp/backend/care_period_auth.py).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import care
from core.user_manager import get_user_id
from miniapp.backend.care_period_auth import care_period_gate

logger = logging.getLogger("miniapp.care_period_auto")
router = APIRouter()


class AutoPeriodBody(BaseModel):
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None


def _json(p: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    return {"starts_on": p["starts_on"].isoformat(), "ends_on": p["ends_on"].isoformat(), "source": p["source"]} if p else None


@router.put("/care/period/auto")
async def put_auto_period(body: AutoPeriodBody, owner_tg: int = Depends(care_period_gate)) -> Dict[str, Any]:
    user_id = (await get_user_id(owner_tg)) or ""
    if not user_id:
        raise HTTPException(status_code=404, detail="not found")
    try:
        res = await asyncio.to_thread(care.apply_auto_period, user_id, body.starts_on, body.ends_on)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    logger.info("care period auto: applied=%s changed=%s", res["applied"], res["changed"])
    return {"ok": True, "applied": res["applied"], "changed": res["changed"], "reason": res.get("reason"), "period": _json(res["period"])}
