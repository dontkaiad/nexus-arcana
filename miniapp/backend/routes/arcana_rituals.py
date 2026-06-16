"""miniapp/backend/routes/arcana_rituals.py — GET /api/arcana/rituals, /{id}.

Ритуалы — чтение через PG (vertical slice). Клиентская атрибуция недоступна:
PG-ритуалы имеют client_id=NULL пока клиенты не мигрированы (см. rituals_tables.py).
"""
from __future__ import annotations

import logging
from datetime import timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from arcana.repos.pg_rituals_repo import (
    PgRitualsRepo,
    CODE_TO_GOAL,
    CODE_TO_PLACE,
    CODE_TO_RESULT,
    CODE_TO_TYPE,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import today_user_tz
from miniapp.backend.routes._arcana_common import parse_supplies, split_lines

logger = logging.getLogger("miniapp.arcana.rituals")

router = APIRouter()
_rituals_repo = PgRitualsRepo()

# Reverse map для ?goal= фильтра: display label → PG code
_GOAL_LABEL_TO_CODE = {v: k for k, v in CODE_TO_GOAL.items()}


def _date_str(dt, tz_offset: int) -> Optional[str]:
    if not dt:
        return None
    local = dt.astimezone(timezone(timedelta(hours=tz_offset)))
    return local.date().isoformat()


def _ritual_brief(r, tz_offset: int) -> dict:
    goal_d = CODE_TO_GOAL.get(r.goal or "") or None
    return {
        "id": r.id,
        "name": r.name,
        "goal": goal_d,
        "goals": [goal_d] if goal_d else [],
        "place": CODE_TO_PLACE.get(r.place or "") or None,
        "date": _date_str(r.date, tz_offset),
        "type": CODE_TO_TYPE.get(r.type_code or "") or None,
        "client": "Личный",
        "client_id": None,
        "result": CODE_TO_RESULT.get(r.result or "unverified", "⏳ Не проверено"),
        "price": int(r.price) if r.price else 0,
        "paid": int(r.paid) if r.paid else 0,
    }


@router.get("/arcana/rituals")
async def list_rituals(
    tg_id: int = Depends(current_user_id),
    goal: Optional[str] = Query(None, description="фильтр по Цели, напр. '🛡️ Защита'"),
    client_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _, tz_offset = await today_user_tz(tg_id)

    try:
        entries = await _rituals_repo.list_all(user_notion_id)
    except Exception as e:
        logger.warning("rituals list_all failed: %s", e)
        return {"total": 0, "rituals": []}

    goal_code = _GOAL_LABEL_TO_CODE.get(goal) if goal else None

    items: list = []
    for r in entries:
        # client_id filter: PG rituals have NULL client_id until clients migrate
        if client_id and r.client_id != client_id:
            continue
        if goal_code and r.goal != goal_code:
            continue
        items.append(_ritual_brief(r, tz_offset))

    return {"total": len(items), "rituals": items}


@router.get("/arcana/rituals/{ritual_id}")
async def ritual_detail(
    ritual_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    _, tz_offset = await today_user_tz(tg_id)
    try:
        r = await _rituals_repo.find_by_id(ritual_id)
    except Exception:
        raise HTTPException(status_code=404, detail="ritual not found")
    if not r:
        raise HTTPException(status_code=404, detail="ritual not found")

    goal_d = CODE_TO_GOAL.get(r.goal or "") or None
    supplies, supplies_total = parse_supplies(r.consumables or "")
    structure = split_lines(r.structure or "")

    return {
        "id": r.id,
        "name": r.name,
        "client": "Личный",
        "client_id": None,
        "question": None,
        "goal": goal_d,
        "goals": [goal_d] if goal_d else [],
        "place": CODE_TO_PLACE.get(r.place or "") or None,
        "date": _date_str(r.date, tz_offset),
        "type": CODE_TO_TYPE.get(r.type_code or "") or None,
        "price": int(r.price) if r.price else 0,
        "paid": int(r.paid) if r.paid else 0,
        "time_min": r.time_min,
        "supplies": supplies,
        "supplies_total": supplies_total,
        "offerings": r.offerings or None,
        "powers": r.powers or None,
        "structure": structure,
        "notes": r.notes or None,
        "result": CODE_TO_RESULT.get(r.result or "unverified", "⏳ Не проверено"),
        "photo_url": r.photo_url or None,
    }
