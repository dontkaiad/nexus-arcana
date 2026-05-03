"""miniapp/backend/routes/arcana_rituals.py — GET /api/arcana/rituals, /{id}."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.notion_client import get_page, rituals_all
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    multi_select_names,
    number_of,
    relation_ids_of,
    rich_text_plain,
    select_of,
    title_plain,
    to_local_date,
    today_user_tz,
)
from miniapp.backend.routes._arcana_common import (
    client_name_from,
    load_clients_map,
    parse_supplies,
    serialize_ritual_brief,
    split_lines,
)

logger = logging.getLogger("miniapp.arcana.rituals")

router = APIRouter()


@router.get("/arcana/rituals")
async def list_rituals(
    tg_id: int = Depends(current_user_id),
    goal: Optional[str] = Query(None, description="фильтр по Цели, напр. '🛡️ Защита'"),
    client_id: Optional[str] = Query(None),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    _, tz_offset = await today_user_tz(tg_id)
    pages = await rituals_all(user_notion_id=user_notion_id)
    clients_map = await load_clients_map(user_notion_id)

    items: list[dict] = []
    for p in pages:
        if client_id and client_id not in relation_ids_of(p, "👥 Клиенты"):
            continue
        if goal:
            goals = multi_select_names(p, "Цель")
            if not goals:
                single = select_of(p, "Цель")
                goals = [single] if single else []
            if goal not in goals:
                continue
        items.append(serialize_ritual_brief(p, clients_map, tz_offset))

    return {"total": len(items), "rituals": items}


@router.get("/arcana/rituals/{ritual_id}")
async def ritual_detail(
    ritual_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        page = await get_page(ritual_id)
    except Exception:
        raise HTTPException(status_code=404, detail="ritual not found")
    if not page:
        raise HTTPException(status_code=404, detail="ritual not found")

    owners = relation_ids_of(page, "🪪 Пользователи")
    if user_notion_id and user_notion_id not in owners:
        raise HTTPException(status_code=404, detail="ritual not found")

    clients_map = await load_clients_map(user_notion_id)
    _, tz_offset = await today_user_tz(tg_id)

    # Цель — multi_select с fallback на select
    goals = multi_select_names(page, "Цель")
    if not goals:
        single = select_of(page, "Цель")
        goals = [single] if single else []

    consumables_raw = rich_text_plain(page, "Расходники")
    supplies, supplies_total = parse_supplies(consumables_raw)

    # Структура: сначала попробуем Notion "Структура" как rich_text, разобьём по \n
    structure = split_lines(rich_text_plain(page, "Структура"))

    # Подношения: schema.py зовёт "Подношения", notion_client писал "Подношения/Откуп"
    offerings = (
        rich_text_plain(page, "Подношения/Откуп")
        or rich_text_plain(page, "Подношения")
    ) or None

    client_name, cid = client_name_from(page, clients_map)
    deadline_raw = (page.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
    date_local = to_local_date(deadline_raw, tz_offset)

    return {
        "id": page.get("id", ""),
        "name": title_plain(page, "Название"),
        "client": client_name,
        "client_id": cid,
        "question": None,  # В схеме Notion поля нет — см. R3
        "goal": goals[0] if goals else None,
        "goals": goals,
        "place": select_of(page, "Место") or None,
        "date": date_local.isoformat() if date_local else None,
        "type": select_of(page, "Тип") or None,
        "price": int(round(number_of(page, "Цена за ритуал"))),
        "paid": int(round(number_of(page, "Оплачено"))),
        "time_min": int(round(number_of(page, "Время (мин)"))) or None,
        "supplies": supplies,
        "supplies_total": supplies_total,
        "offerings": offerings,
        "powers": rich_text_plain(page, "Силы") or None,
        "structure": structure,
        "notes": rich_text_plain(page, "Заметки") or None,
        "result": select_of(page, "Результат") or "⏳ Не проверено",
        "photo_url": (page.get("properties", {}).get("Фото", {}) or {}).get("url") or None,
    }
