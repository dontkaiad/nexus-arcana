"""miniapp/backend/routes/arcana_debts.py — GET /api/arcana/debts.

Возвращает разбивку «кто что должен»:
- money: сессии/ритуалы где (Сумма||Цена за ритуал) > Оплачено, group by клиент.
- barter: открытые items из 🗒️ Списки (Тип=📋 Чеклист, Категория=🔄 Бартер,
  Статус ≠ Done/Archived), сматченные на расклад/ритуал по полю «Группа»
  (rich_text == title записи), оттуда group by клиент.
Self-client (Тип=🌟 Self) исключается.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends

from core.config import config
from core.notion_client import (
    _with_user_filter,
    arcana_clients_summary,
    query_pages,
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
    status_name,
    title_plain,
    title_text,
)

logger = logging.getLogger("miniapp.arcana.debts")

router = APIRouter()


def _type_icon(client_type: str) -> str:
    s = (client_type or "").strip()
    return s.split()[0] if s else ""


def _is_self(ctype_full: str) -> bool:
    return "Self" in (ctype_full or "") or (ctype_full or "").startswith("🌟")


async def _fetch_open_barter_items(user_notion_id: str) -> list[dict]:
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


def _build_money(
    sessions: list[dict],
    rituals: list[dict],
    clients_by_id: dict[str, dict],
) -> list[dict]:
    """Список долгов по клиентам (только money). Self-client исключён."""
    by_client: dict[str, dict] = {}
    for p in sessions:
        ids = relation_ids_of(p, "👥 Клиенты")
        if not ids:
            continue
        cid = ids[0]
        c = clients_by_id.get(cid)
        if not c or _is_self(c["type_full"]):
            continue
        price = number_of(p, "Сумма")
        paid = number_of(p, "Оплачено")
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
            "id": p.get("id", ""),
            "kind": "session",
            "desc": (title_plain(p, "Тема") or "Сеанс")[:120],
            "amount": int(round(price)),
            "paid": int(round(paid)),
        })
    for p in rituals:
        ids = relation_ids_of(p, "👥 Клиенты")
        if not ids:
            continue
        cid = ids[0]
        c = clients_by_id.get(cid)
        if not c or _is_self(c["type_full"]):
            continue
        price = number_of(p, "Цена за ритуал")
        paid = number_of(p, "Оплачено")
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
            "id": p.get("id", ""),
            "kind": "ritual",
            "desc": (title_plain(p, "Название") or "Ритуал")[:120],
            "amount": int(round(price)),
            "paid": int(round(paid)),
        })
    out = list(by_client.values())
    for b in out:
        b["amount"] = int(round(b["amount"]))
    out.sort(key=lambda x: x["amount"], reverse=True)
    return out


def _build_barter(
    barter_items: list[dict],
    sessions: list[dict],
    rituals: list[dict],
    clients_by_id: dict[str, dict],
) -> list[dict]:
    """Группировка открытых бартер-чеклистов по клиенту через title-match."""
    # title (lower-stripped) → first matching record's client_id
    # TODO: при коллизии (одно название у разных записей) берём первый match.
    title_to_client: dict[str, str] = {}
    for p in sessions:
        t = (title_plain(p, "Тема") or "").strip().lower()
        if not t or t in title_to_client:
            continue
        ids = relation_ids_of(p, "👥 Клиенты")
        if ids:
            title_to_client[t] = ids[0]
    for p in rituals:
        t = (title_plain(p, "Название") or "").strip().lower()
        if not t or t in title_to_client:
            continue
        ids = relation_ids_of(p, "👥 Клиенты")
        if ids:
            title_to_client[t] = ids[0]

    by_client: dict[str, dict] = {}
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
    # Сначала привязанные клиенты по кол-ву items, orphan ('') в конец.
    out.sort(key=lambda x: (x["client_id"] == "", -len(x["items"])))
    return out


@router.get("/arcana/debts")
async def list_debts(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    clients_pages = await arcana_clients_summary(user_notion_id=user_notion_id)
    sessions = await sessions_all(user_notion_id=user_notion_id)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    barter_items = await _fetch_open_barter_items(user_notion_id)

    clients_by_id: dict[str, dict] = {}
    for c in clients_pages:
        ctype_full = (c.get("properties", {}).get("Тип клиента", {}) or {}).get("select")
        ctype_full = ctype_full.get("name", "") if ctype_full else ""
        clients_by_id[c["id"]] = {
            "name": title_plain(c, "Имя"),
            "type_full": ctype_full,
        }

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
