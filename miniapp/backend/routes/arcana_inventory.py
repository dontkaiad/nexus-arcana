"""miniapp/backend/routes/arcana_inventory.py — Arcana inventory tab.

Делегирует в core/list_manager (тип "📦 Инвентарь", бот "🌒 Arcana").
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.config import config
from core.list_manager import (
    CATEGORY_TO_FINANCE,
    _today_iso,
)
from core.notion_client import (
    _date,
    _number,
    _select,
    _status,
    _text,
    finance_add,
    get_page,
    query_pages,
    update_page,
)
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_ARCANA,
    select_name,
)

logger = logging.getLogger("miniapp.arcana.inventory")

router = APIRouter()

# Категории Arcana-инвентаря (тот же набор что в LIST_CATEGORIES для практики)
ARCANA_INV_CATEGORIES = [
    "🕯️ Расходники",
    "🌿 Травы/Масла",
    "🃏 Карты/Колоды",
    "💳 Прочее",
]


def _serialize(page: dict) -> dict:
    props = page.get("properties", {})
    title_arr = (props.get("Название", {}) or {}).get("title") or []
    name = "".join(t.get("plain_text", "") for t in title_arr).strip()
    qty = (props.get("Количество", {}) or {}).get("number")
    price = (props.get("Цена", {}) or {}).get("number")
    note_arr = (props.get("Заметка", {}) or {}).get("rich_text") or []
    note = "".join(t.get("plain_text", "") for t in note_arr).strip() or None
    expires = (props.get("Срок годности", {}) or {}).get("date") or {}
    cat = select_name(props.get("Категория", {})) or None
    return {
        "id": page.get("id", ""),
        "name": name,
        "cat": cat,
        "qty": qty,
        "price": price,
        "note": note,
        "expires": expires.get("start") or None,
    }


async def _fetch_arcana_inventory(user_notion_id: str) -> list[dict]:
    db_id = config.db_lists
    if not db_id:
        return []
    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": BOT_ARCANA}},
        {"property": "Тип", "select": {"equals": "📦 Инвентарь"}},
        {"property": "Статус", "status": {"does_not_equal": "Archived"}},
    ]
    if user_notion_id:
        conditions.append({
            "or": [
                {"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}},
                {"property": "🪪 Пользователи", "relation": {"is_empty": True}},
            ]
        })
    try:
        pages = await query_pages(
            db_id,
            filters={"and": conditions},
            sorts=[{"property": "Срок годности", "direction": "ascending"}],
            page_size=300,
        )
    except Exception as e:
        logger.warning("arcana inventory query failed: %s", e)
        return []
    return pages


@router.get("/arcana/inventory")
async def list_inventory(
    tg_id: int = Depends(current_user_id),
    cat: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    pages = await _fetch_arcana_inventory(user_notion_id)
    items: list[dict] = []
    counts: dict[str, int] = {c: 0 for c in ARCANA_INV_CATEGORIES}
    needle = (q or "").lower().strip()
    for p in pages:
        it = _serialize(p)
        if it["cat"]:
            counts[it["cat"]] = counts.get(it["cat"], 0) + 1
        if cat and it["cat"] != cat:
            continue
        if needle and needle not in (it["name"] or "").lower():
            continue
        items.append(it)
    cats_out = [{"name": n, "count": counts.get(n, 0)} for n in ARCANA_INV_CATEGORIES]
    extras = sorted(k for k in counts if k not in ARCANA_INV_CATEGORIES)
    cats_out.extend({"name": k, "count": counts[k]} for k in extras)
    return {"items": items, "categories": cats_out}


@router.get("/arcana/inventory/categories")
async def inventory_categories(
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    pages = await _fetch_arcana_inventory(user_notion_id)
    counts: dict[str, int] = {c: 0 for c in ARCANA_INV_CATEGORIES}
    for p in pages:
        it = _serialize(p)
        if it["cat"]:
            counts[it["cat"]] = counts.get(it["cat"], 0) + 1
    return {"categories": [
        {"name": n, "count": counts.get(n, 0)} for n in ARCANA_INV_CATEGORIES
    ]}


# ── Mutations ────────────────────────────────────────────────────────────────

class InventoryEditBody(BaseModel):
    qty: Optional[float] = None
    note: Optional[str] = None
    expires: Optional[str] = None  # YYYY-MM-DD; пусто = очистить


async def _ensure_owned(item_id: str, user_notion_id: str) -> dict:
    """Подгрузить страницу + проверить что это инвентарь Arcana этого юзера."""
    from miniapp.backend._helpers import relation_ids_of
    page = await get_page(item_id)
    if not page:
        raise HTTPException(status_code=404, detail="not found")
    props = page.get("properties", {})
    if select_name(props.get("Бот", {})) != BOT_ARCANA:
        raise HTTPException(status_code=404, detail="not found")
    if select_name(props.get("Тип", {})) != "📦 Инвентарь":
        raise HTTPException(status_code=404, detail="not an inventory item")
    if user_notion_id and user_notion_id not in relation_ids_of(page, "🪪 Пользователи"):
        # допускаем айтемы без owner relation (legacy)
        owners = relation_ids_of(page, "🪪 Пользователи")
        if owners:
            raise HTTPException(status_code=404, detail="not found")
    return page


@router.patch("/arcana/inventory/{item_id}")
async def edit_inventory(
    item_id: str,
    body: InventoryEditBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _ensure_owned(item_id, user_notion_id)
    props: dict = {}
    if body.qty is not None:
        props["Количество"] = _number(float(body.qty))
    if body.note is not None:
        props["Заметка"] = _text(body.note)
    if body.expires is not None:
        props["Срок годности"] = _date(body.expires) if body.expires else {"date": None}
    if not props:
        return {"ok": True, "noop": True}
    await update_page(item_id, props)
    return {"ok": True}


class InventoryPurchaseBody(BaseModel):
    price: float = Field(gt=0)
    qty_added: Optional[float] = None  # если задано — приплюсуем к Количеству
    description: Optional[str] = None


@router.post("/arcana/inventory/{item_id}/purchase")
async def purchase_inventory(
    item_id: str,
    body: InventoryPurchaseBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """«Купила» — append в Финансы (Бот=Arcana, кат=🕯️ Расходники) +
    приплюсовать qty в инвентарь."""
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _ensure_owned(item_id, user_notion_id)
    props = page.get("properties", {})
    title = "".join(
        t.get("plain_text", "")
        for t in (props.get("Название", {}) or {}).get("title") or []
    ).strip() or "покупка"
    cat = select_name(props.get("Категория", {})) or "💳 Прочее"
    finance_cat = CATEGORY_TO_FINANCE.get(cat, "🕯️ Расходники")
    fin_id = await finance_add(
        date=_today_iso(),
        amount=float(body.price),
        category=finance_cat,
        type_="💸 Расход",
        source="💳 Карта",
        description=body.description or title,
        bot_label=BOT_ARCANA,
        user_notion_id=user_notion_id,
    )
    if body.qty_added is not None and body.qty_added > 0:
        cur = (props.get("Количество", {}) or {}).get("number") or 0
        await update_page(item_id, {"Количество": _number(float(cur) + float(body.qty_added))})
    return {
        "ok": True,
        "finance_id": fin_id,
        "finance_category": finance_cat,
        "name": title,
    }


class InventoryDepletedBody(BaseModel):
    add_to_buy: bool = False


@router.post("/arcana/inventory/{item_id}/depleted")
async def depleted_inventory(
    item_id: str,
    body: InventoryDepletedBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    """Закончился — статус Archived. Опционально создать айтем в 🛒 Покупки."""
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    page = await _ensure_owned(item_id, user_notion_id)
    await update_page(item_id, {"Статус": _status("Archived")})
    buy_id = None
    if body.add_to_buy:
        from core.list_manager import add_items
        title = "".join(
            t.get("plain_text", "")
            for t in (page.get("properties", {}).get("Название", {}) or {}).get("title") or []
        ).strip()
        cat = select_name(page.get("properties", {}).get("Категория", {})) or None
        if title:
            created = await add_items(
                [{"name": title, "category": cat}],
                "🛒 Покупки",
                BOT_ARCANA,
                user_notion_id,
            )
            if created:
                buy_id = created[0].get("id")
    return {"ok": True, "archived": True, "buy_id": buy_id}
