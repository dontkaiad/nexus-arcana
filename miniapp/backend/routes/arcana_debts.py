"""miniapp/backend/routes/arcana_debts.py — GET /api/arcana/debts.

Возвращает разбивку «кто что должен»:
- money: сессии/ритуалы где (amount||price) > paid, group by клиент.
  Sessions и clients — PG. Rituals — PG (rituals slice).
- barter: открытые items из 🗒️ Списки (Тип=📋 Чеклист, Категория=🔄 Бартер,
  Статус ≠ Done/Archived), сматченные на расклад/ритуал по полю «Группа»
  (rich_text == title записи). Lists остаётся в Notion.
Self-client (type_code=self) исключается.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends

from arcana.repos.clients_repo import ClientsRepo
from arcana.repos.pg_clients_repo import TYPE_CODE_TO_FULL
from arcana.repos.pg_rituals_repo import PgRitualsRepo
from arcana.repos.pg_sessions_repo import PgSessionsRepo
from core.config import config
from core.notion_client import (
    _with_user_filter,
    query_pages,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    rich_text_plain,
    title_text,
)

logger = logging.getLogger("miniapp.arcana.debts")

router = APIRouter()

_clients_repo = ClientsRepo()
_rituals_repo = PgRitualsRepo()
_sessions_repo = PgSessionsRepo()


def _type_icon(type_full: str) -> str:
    s = (type_full or "").strip()
    return s.split()[0] if s else ""


def _is_self(type_full: str) -> bool:
    return "Self" in (type_full or "") or (type_full or "").startswith("🌟")


async def _fetch_open_barter_items(user_notion_id: str) -> list:
    """Открытые items 🗒️ Списки.Категория=🔄 Бартер. Возвращает сырые pages."""
    db_id = config.db_lists
    if not db_id:
        return []
    base = {
        "and": [
            {"property": "Тип", "select": {"equals": "📋 Чеклист"}},
            {"property": "Категория", "select": {"equals": "🔄 Бартер"}},
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        ]
    }
    filters = _with_user_filter(base, user_notion_id)
    try:
        return await query_pages(db_id, filters=filters, page_size=300)
    except Exception as e:
        logger.warning("barter items fetch failed: %s", e)
        return []


def _build_money(sessions, rituals, clients_by_id: dict) -> list:
    """Список долгов по клиентам (только money). Self-client исключён.

    sessions: List[TripletEntry] (PG)
    rituals:  List[RitualEntry] (PG)
    clients_by_id: {pg_client_id: {"name": ..., "type_full": ...}}
    """
    by_client: dict = {}
    for t in sessions:
        cid = t.client_id
        if not cid:
            continue
        c = clients_by_id.get(cid)
        if not c or _is_self(c["type_full"]):
            continue
        amount = float(t.amount or 0)
        paid = float(t.paid or 0)
        debt = amount - paid
        if debt <= 0:
            continue
        bucket = by_client.setdefault(cid, {
            "client_id": cid,
            "client_name": c["name"],
            "client_type": _type_icon(c["type_full"]),
            "amount": 0,
            "items": [],
        })
        bucket["amount"] += debt
        bucket["items"].append({
            "id": t.id,
            "kind": "session",
            "desc": (t.question or "Сеанс")[:120],
            "amount": int(round(amount)),
            "paid": int(round(paid)),
        })
    for r in rituals:
        cid = r.client_id
        if not cid:
            continue
        c = clients_by_id.get(cid)
        if not c or _is_self(c["type_full"]):
            continue
        price = float(r.price or 0)
        paid = float(r.paid or 0)
        debt = price - paid
        if debt <= 0:
            continue
        bucket = by_client.setdefault(cid, {
            "client_id": cid,
            "client_name": c["name"],
            "client_type": _type_icon(c["type_full"]),
            "amount": 0,
            "items": [],
        })
        bucket["amount"] += debt
        bucket["items"].append({
            "id": r.id,
            "kind": "ritual",
            "desc": (r.name or "Ритуал")[:120],
            "amount": int(round(price)),
            "paid": int(round(paid)),
        })
    out = list(by_client.values())
    for b in out:
        b["amount"] = int(round(b["amount"]))
    out.sort(key=lambda x: x["amount"], reverse=True)
    return out


def _build_barter(barter_items: list, sessions, rituals, clients_by_id: dict) -> list:
    """Группировка открытых бартер-чеклистов по клиенту через title-match.

    sessions: List[TripletEntry] (PG) — title = question, client = client_id
    rituals:  List[RitualEntry] (PG)
    barter_items: Notion 🗒️ Списки pages (still Notion)
    """
    title_to_client: dict = {}
    for t in sessions:
        title = (t.question or "").strip().lower()
        if not title or title in title_to_client:
            continue
        if t.client_id:
            title_to_client[title] = t.client_id
    for r in rituals:
        title = (r.name or "").strip().lower()
        if not title or title in title_to_client:
            continue
        if r.client_id:
            title_to_client[title] = r.client_id

    by_client: dict = {}
    for it in barter_items:
        props = it.get("properties", {})
        name = title_text(props.get("Название", {}))
        group = rich_text_plain(it, "Группа")
        gkey = group.strip().lower()
        cid = title_to_client.get(gkey, "") if gkey else ""
        c = clients_by_id.get(cid) if cid else None
        if c and _is_self(c["type_full"]):
            continue
        if c:
            client_name = c["name"]
            client_type = _type_icon(c["type_full"])
        else:
            cid = ""
            client_name = "—"
            client_type = ""
        bucket = by_client.setdefault(cid, {
            "client_id": cid,
            "client_name": client_name,
            "client_type": client_type,
            "items": [],
        })
        bucket["items"].append({
            "id": it.get("id", ""),
            "name": name,
            "group": group or None,
        })
    out = list(by_client.values())
    out.sort(key=lambda x: (x["client_id"] == "", -len(x["items"])))
    return out


@router.get("/arcana/debts")
async def list_debts(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    clients_list = await _clients_repo.list_all(user_notion_id)
    clients_by_id: dict = {}
    for c in clients_list:
        type_full = TYPE_CODE_TO_FULL.get(c.type_code or "", "")
        clients_by_id[c.id] = {"name": c.name or "", "type_full": type_full}

    sessions = await _sessions_repo.list_all(user_notion_id=user_notion_id)
    rituals = await _rituals_repo.list_all(user_notion_id)
    barter_items = await _fetch_open_barter_items(user_notion_id)

    money = _build_money(sessions, rituals, clients_by_id)
    barter = _build_barter(barter_items, sessions, rituals, clients_by_id)

    money_total = sum(b["amount"] for b in money)
    barter_total = sum(len(b["items"]) for b in barter)

    return {
        "money": money,
        "barter": barter,
        "totals": {
            "money": int(round(money_total)),
            "barter_items": barter_total,
        },
    }
