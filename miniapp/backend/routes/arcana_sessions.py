"""miniapp/backend/routes/arcana_sessions.py — GET /api/arcana/sessions, /{id}."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.notion_client import get_page, sessions_all
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    first_emoji,
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
    extract_bottom_from_interp,
    load_clients_map,
    serialize_session_brief,
    split_cards_raw,
    client_name_from,
)

logger = logging.getLogger("miniapp.arcana.sessions")

router = APIRouter()


def _parse_filter(filter_str: str) -> dict:
    """'area:Отношения|client_id:xxx|status:unchecked' → {area:..., client_id:..., status:...}.

    'all' (default) → {}.
    """
    if not filter_str or filter_str == "all":
        return {}
    out: dict = {}
    for chunk in filter_str.split("|"):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        out[k.strip()] = v.strip()
    return out


@router.get("/arcana/sessions")
async def list_sessions(
    tg_id: int = Depends(current_user_id),
    filter: str = Query("all"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    filters = _parse_filter(filter)
    today_date, tz_offset = await today_user_tz(tg_id)
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    sbylos_filter: Optional[str] = None
    if filters.get("status") == "unchecked":
        sbylos_filter = "⏳ Не проверено"

    all_pages = await sessions_all(user_notion_id=user_notion_id,
                                    sbylos_filter=sbylos_filter)
    clients_map = await load_clients_map(user_notion_id)

    items: list[dict] = []
    area_f = filters.get("area")
    client_f = filters.get("client_id")
    for p in all_pages:
        if client_f and client_f not in relation_ids_of(p, "👥 Клиенты"):
            continue
        if area_f and area_f not in multi_select_names(p, "Область"):
            continue
        items.append(serialize_session_brief(p, clients_map, tz_offset))

    return {
        "filter": filter,
        "total": len(items),
        "sessions": items[:limit],
    }


@router.get("/arcana/sessions/{session_id}")
async def session_detail(
    session_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        page = await get_page(session_id)
    except Exception as e:
        logger.warning("session get_page failed: %s", e)
        raise HTTPException(status_code=404, detail="session not found")
    if not page:
        raise HTTPException(status_code=404, detail="session not found")

    # Проверка owner
    owners = relation_ids_of(page, "🪪 Пользователи")
    if user_notion_id and user_notion_id not in owners:
        raise HTTPException(status_code=404, detail="session not found")

    clients_map = await load_clients_map(user_notion_id)
    _, tz_offset = await today_user_tz(tg_id)

    interp_raw = rich_text_plain(page, "Трактовка")
    bottom_name, interp_cleaned = extract_bottom_from_interp(interp_raw)
    cards_raw = rich_text_plain(page, "Карты")
    cards = split_cards_raw(cards_raw)

    client_name, client_id = client_name_from(page, clients_map)
    session_type = select_of(page, "Тип сеанса")
    self_client = (session_type == "🌟 Личный") and not relation_ids_of(page, "👥 Клиенты")

    deadline_raw = (page.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
    date_local = to_local_date(deadline_raw, tz_offset)

    photo_url = (page.get("properties", {}).get("Фото", {}).get("url")) or None

    return {
        "id": page.get("id", ""),
        "question": title_plain(page, "Тема"),
        "client": client_name,
        "client_id": client_id,
        "self_client": self_client,
        "area": multi_select_names(page, "Область"),
        "deck": ", ".join(multi_select_names(page, "Колоды")) or None,
        "type": (multi_select_names(page, "Тип расклада") or [None])[0],
        "date": date_local.isoformat() if date_local else None,
        "cards_raw": cards_raw or None,
        "cards": cards,
        "bottom": (
            {"name": bottom_name, "icon": first_emoji(bottom_name) or None}
            if bottom_name else None
        ),
        "interpretation": interp_cleaned or None,
        "done": select_of(page, "Сбылось") or "⏳ Не проверено",
        "price": int(round(number_of(page, "Сумма"))),
        "paid": int(round(number_of(page, "Оплачено"))),
        "photo_url": photo_url,
    }
