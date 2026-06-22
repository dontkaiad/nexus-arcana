"""miniapp/backend/routes/arcana_clients.py — GET /api/arcana/clients, /{id}.

Clients — чтение через PG (vertical slice). ID-контракт: PG int as str.
sessions/rituals агрегируются из PG repos по client_id.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from arcana.repos.clients_repo import ClientsRepo
from arcana.repos.pg_clients_repo import TYPE_CODE_TO_FULL, STATUS_CODE_TO_LABEL
from arcana.repos.pg_rituals_repo import PgRitualsRepo
from arcana.repos.pg_sessions_repo import PgSessionsRepo
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.arcana.clients")

router = APIRouter()

_clients_repo = ClientsRepo()
_rituals_repo = PgRitualsRepo()
_sessions_repo = PgSessionsRepo()


def _initial(name: str) -> str:
    return name.strip()[:1].upper() if name else "?"


def _type_icon(type_full: str) -> str:
    s = (type_full or "").strip()
    return s.split()[0] if s else ""


@router.get("/arcana/clients")
async def list_clients(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    clients_list = await _clients_repo.list_all(user_notion_id)
    all_sessions = await _sessions_repo.list_all(user_notion_id)
    all_rituals = await _rituals_repo.list_all(user_notion_id)

    # Aggregate stats by PG client_id (str)
    agg: dict = {}
    for s in all_sessions:
        cid = s.client_id or ""
        b = agg.setdefault(cid, {"sessions": set(), "rituals": 0, "debt": 0.0, "paid": 0.0})
        # Считаем СЕССИИ, не триплеты: триплеты одной сессии = один сеанс (#164).
        b["sessions"].add(
            s.session_name.strip().lower() if s.session_name else f"solo:{s.id}"
        )
        amt = float(s.amount or 0)
        pd = float(s.paid or 0)
        b["paid"] += pd
        if amt > pd:
            b["debt"] += amt - pd
    for r in all_rituals:
        cid = r.client_id or ""
        b = agg.setdefault(cid, {"sessions": set(), "rituals": 0, "debt": 0.0, "paid": 0.0})
        b["rituals"] += 1
        price = float(r.price or 0)
        pd = float(r.paid or 0)
        b["paid"] += pd
        if price > pd:
            b["debt"] += price - pd

    out = []
    total_debt = 0.0
    total_paid_all = 0.0
    for c in clients_list:
        b = agg.get(c.id, {"sessions": set(), "rituals": 0, "debt": 0.0, "paid": 0.0})
        type_full = TYPE_CODE_TO_FULL.get(c.type_code or "", "")
        debt = int(round(b["debt"]))
        total_debt += debt
        total_paid_all += b["paid"]
        out.append({
            "id": c.id,
            "name": c.name,
            "initial": _initial(c.name),
            "status": STATUS_CODE_TO_LABEL.get(c.status_code or "", ""),
            "type": _type_icon(type_full),
            "type_full": type_full,
            "sessions_count": len(b["sessions"]),
            "rituals_count": b["rituals"],
            "debt": debt,
            "barter_count": 0,
            "total_paid": int(round(b["paid"])),
            "photo_url": c.photo_url,
        })
    out.sort(key=lambda x: x["name"])
    return {
        "total": len(out),
        "total_debt": int(round(total_debt)),
        "total_paid_all": int(round(total_paid_all)),
        "clients": out,
    }


@router.get("/arcana/clients/{client_id}")
async def client_dossier(
    client_id: str,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    c = await _clients_repo.find_by_id(client_id)
    if not c:
        raise HTTPException(status_code=404, detail="client not found")

    # Load all sessions for this user, filter by client_id (gets TripletEntry with id + amount/paid)
    all_sessions = await _sessions_repo.list_all(user_notion_id)
    my_sessions = [s for s in all_sessions if s.client_id == client_id]
    my_rituals = await _rituals_repo.list_by_client(client_id)

    debt = 0.0
    total_paid = 0.0
    for s in my_sessions:
        amt = float(s.amount or 0)
        pd = float(s.paid or 0)
        total_paid += pd
        if amt > pd:
            debt += amt - pd
    for r in my_rituals:
        price = float(r.price or 0)
        pd = float(r.paid or 0)
        total_paid += pd
        if price > pd:
            debt += price - pd

    since = None
    for s in my_sessions:
        if s.date and (since is None or s.date < since):
            since = s.date

    history = []
    for s in my_sessions:
        amt = int(round(float(s.amount or 0)))
        pd = int(round(float(s.paid or 0)))
        history.append({
            "id": s.id,
            "date": s.date or None,
            "kind": "session",
            "desc": (s.question[:120] if s.question else "Сеанс"),
            "amount": amt,
            "paid": pd >= amt and amt > 0,
        })
    for r in my_rituals:
        price = int(round(float(r.price or 0)))
        pd = int(round(float(r.paid or 0)))
        history.append({
            "id": r.id,
            "date": r.date.strftime("%Y-%m-%d") if r.date else None,
            "kind": "ritual",
            "desc": (r.name[:120] if r.name else "Ритуал"),
            "amount": price,
            "paid": pd >= price and price > 0,
        })
    history.sort(key=lambda e: e["date"] or "", reverse=True)
    history = history[:20]

    type_full = TYPE_CODE_TO_FULL.get(c.type_code or "", "")
    from core.client_object_photos import parse as _parse_objects
    photos = _parse_objects(c.object_photos or "")

    return {
        "id": client_id,
        "name": c.name,
        "initial": _initial(c.name),
        "status": STATUS_CODE_TO_LABEL.get(c.status_code or "", ""),
        "type": _type_icon(type_full),
        "type_full": type_full,
        "contact": c.contact or None,
        "since": since,
        "birthday": c.birthday,
        "request": c.request or None,
        "notes": c.notes or None,
        "photo_url": c.photo_url,
        "photos": photos,
        "stats": {
            # СЕССИИ, не триплеты (триплеты одной сессии = один сеанс), #164.
            "sessions": len({
                s.session_name.strip().lower() if s.session_name else f"solo:{s.id}"
                for s in my_sessions
            }),
            "rituals": len(my_rituals),
            "total_paid": int(round(total_paid)),
            "debt": int(round(debt)),
        },
        "history": history,
    }
