"""miniapp/backend/routes/arcana_clients.py — GET /api/arcana/clients, /{id}."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.notion_client import (
    arcana_clients_summary,
    get_page,
    rituals_all,
    sessions_all,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    number_of,
    relation_ids_of,
    rich_text_plain,
    select_of,
    title_plain,
)

logger = logging.getLogger("miniapp.arcana.clients")

router = APIRouter()


def _client_status(page: dict) -> str:
    st = (page.get("properties", {}).get("Статус", {}).get("status") or {}).get("name", "")
    if not st:
        # status может быть select в некоторых схемах
        st = select_of(page, "Статус")
    return st or ""


def _initial(name: str) -> str:
    return name.strip()[:1].upper() if name else "?"


def _extract_ts(page: dict) -> str:
    """Берём дату сессии или created_time page'а для sort'а истории."""
    raw = (page.get("properties", {}).get("Дата", {}).get("date") or {}).get("start", "")
    return raw[:10] if raw else (page.get("created_time") or "")[:10]


def _has_barter(page: dict) -> bool:
    """«Бартер · что» — rich_text. Считаем заполненным если есть текст."""
    return bool(rich_text_plain(page, "Бартер · что").strip())


def _aggregate_by_client(sessions: list[dict], rituals: list[dict]) -> dict:
    """→ {client_id: {sessions, rituals, debt, total_paid, barter_count}}.
    Записи без клиента — в ключе ''.
    """
    agg: dict[str, dict] = {}
    for p in sessions:
        ids = relation_ids_of(p, "👥 Клиенты")
        cid = ids[0] if ids else ""
        bucket = agg.setdefault(cid, {"sessions": [], "rituals": [],
                                       "debt": 0.0, "total_paid": 0.0,
                                       "barter_count": 0})
        price = number_of(p, "Сумма")
        paid = number_of(p, "Оплачено")
        bucket["sessions"].append(p)
        bucket["total_paid"] += paid
        if price - paid > 0:
            bucket["debt"] += (price - paid)
        if _has_barter(p):
            bucket["barter_count"] += 1
    for p in rituals:
        ids = relation_ids_of(p, "👥 Клиенты")
        cid = ids[0] if ids else ""
        bucket = agg.setdefault(cid, {"sessions": [], "rituals": [],
                                       "debt": 0.0, "total_paid": 0.0,
                                       "barter_count": 0})
        price = number_of(p, "Цена за ритуал")
        paid = number_of(p, "Оплачено")
        bucket["rituals"].append(p)
        bucket["total_paid"] += paid
        if price - paid > 0:
            bucket["debt"] += (price - paid)
        if _has_barter(p):
            bucket["barter_count"] += 1
    return agg


def _type_icon(client_type: str) -> str:
    """«🌟 Self» / «🤝 Платный» / «🎁 Бесплатный» → первый emoji."""
    s = (client_type or "").strip()
    return s.split()[0] if s else ""


@router.get("/arcana/clients")
async def list_clients(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    clients = await arcana_clients_summary(user_notion_id=user_notion_id)
    sessions = await sessions_all(user_notion_id=user_notion_id)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    agg = _aggregate_by_client(sessions, rituals)

    out: list[dict] = []
    total_debt = 0.0
    total_paid_all = 0.0
    for c in clients:
        cid = c["id"]
        name = title_plain(c, "Имя")
        bucket = agg.get(cid, {"sessions": [], "rituals": [], "debt": 0,
                               "total_paid": 0, "barter_count": 0})
        debt = int(round(bucket["debt"]))
        total_debt += debt
        total_paid_all += bucket["total_paid"]
        # Тип клиента из 👥 Клиенты.«Тип клиента» (select).
        ctype_full = (c.get("properties", {}).get("Тип клиента", {}) or {}).get("select")
        ctype_full = ctype_full.get("name", "") if ctype_full else ""
        photo_url = (c.get("properties", {}).get("Фото", {}) or {}).get("url") or None
        out.append({
            "id": cid,
            "name": name,
            "initial": _initial(name),
            "status": _client_status(c),
            "type": _type_icon(ctype_full),
            "type_full": ctype_full,
            "sessions_count": len(bucket["sessions"]),
            "rituals_count": len(bucket["rituals"]),
            "debt": debt,
            "barter_count": bucket["barter_count"],
            "total_paid": int(round(bucket["total_paid"])),
            "photo_url": photo_url,
        })
    out.sort(key=lambda x: x["name"])
    return {
        "total": len(out),
        "total_debt": int(round(total_debt)),
        "total_paid_all": int(round(total_paid_all)),
        "clients": out,
    }


def _history_entry_session(page: dict) -> dict:
    ts = _extract_ts(page)
    price = int(round(number_of(page, "Сумма")))
    paid_n = int(round(number_of(page, "Оплачено")))
    return {
        "id": page.get("id", ""),
        "date": ts or None,
        "kind": "session",
        "desc": (title_plain(page, "Тема") or "")[:120] or "Сеанс",
        "amount": price,
        "paid": paid_n >= price and price > 0,
    }


def _history_entry_ritual(page: dict) -> dict:
    ts = _extract_ts(page)
    price = int(round(number_of(page, "Цена за ритуал")))
    paid_n = int(round(number_of(page, "Оплачено")))
    return {
        "id": page.get("id", ""),
        "date": ts or None,
        "kind": "ritual",
        "desc": (title_plain(page, "Название") or "")[:120] or "Ритуал",
        "amount": price,
        "paid": paid_n >= price and price > 0,
    }


@router.get("/arcana/clients/{client_id}")
async def client_dossier(
    client_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    try:
        page = await get_page(client_id)
    except Exception:
        raise HTTPException(status_code=404, detail="client not found")
    if not page:
        raise HTTPException(status_code=404, detail="client not found")

    owners = relation_ids_of(page, "🪪 Пользователи")
    if user_notion_id and user_notion_id not in owners:
        raise HTTPException(status_code=404, detail="client not found")

    name = title_plain(page, "Имя")
    ctype_full = (page.get("properties", {}).get("Тип клиента", {}) or {}).get("select")
    ctype_full = ctype_full.get("name", "") if ctype_full else ""
    sessions = await sessions_all(user_notion_id=user_notion_id)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    my_sessions = [p for p in sessions if client_id in relation_ids_of(p, "👥 Клиенты")]
    my_rituals = [p for p in rituals if client_id in relation_ids_of(p, "👥 Клиенты")]

    debt = 0.0
    total_paid = 0.0
    for p in my_sessions:
        total_paid += number_of(p, "Оплачено")
        debt += max(0.0, number_of(p, "Сумма") - number_of(p, "Оплачено"))
    for p in my_rituals:
        total_paid += number_of(p, "Оплачено")
        debt += max(0.0, number_of(p, "Цена за ритуал") - number_of(p, "Оплачено"))

    # "since" — самая ранняя дата среди сессий
    dates = [_extract_ts(p) for p in my_sessions if _extract_ts(p)]
    since = min(dates) if dates else None

    history = (
        [_history_entry_session(p) for p in my_sessions]
        + [_history_entry_ritual(p) for p in my_rituals]
    )
    history.sort(key=lambda e: e["date"] or "", reverse=True)
    history = history[:20]

    return {
        "id": client_id,
        "name": name,
        "initial": _initial(name),
        "status": _client_status(page),
        "type": _type_icon(ctype_full),
        "type_full": ctype_full,
        "contact": rich_text_plain(page, "Контакт") or None,
        "since": since,
        "request": rich_text_plain(page, "Запрос") or None,
        "notes": rich_text_plain(page, "Заметки") or None,
        "photo_url": (page.get("properties", {}).get("Фото", {}).get("url")) or None,
        "stats": {
            "sessions": len(my_sessions),
            "rituals": len(my_rituals),
            "total_paid": int(round(total_paid)),
            "debt": int(round(debt)),
        },
        "history": history,
    }
