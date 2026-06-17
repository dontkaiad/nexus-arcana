"""miniapp/backend/routes/arcana_inventory.py — Arcana inventory tab.

Делегирует в core/list_manager (тип "📦 Инвентарь", бот "🌒 Arcana").
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.list_manager import (
    CATEGORY_TO_FINANCE,
    _today_iso,
)
from core.repos.finance_repo import FinanceRepo
from core.repos.pg_nexus_lists_repo import (
    PgArcanaInventoryRepo as _PgArcanaInventoryRepoClass,
    InventoryItem,
)
from core.user_manager import get_user_notion_id
from core.bot_notify import notify_user

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import BOT_ARCANA

_arcana_inv_repo = _PgArcanaInventoryRepoClass()
_fin_repo = FinanceRepo()

logger = logging.getLogger("miniapp.arcana.inventory")

router = APIRouter()

# Категории Arcana-инвентаря (тот же набор что в LIST_CATEGORIES для практики)
ARCANA_INV_CATEGORIES = [
    "🕯️ Расходники",
    "🌿 Травы/Масла",
    "🃏 Карты/Колоды",
    "💳 Прочее",
]


def _serialize_pg(item: InventoryItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "cat": item.category or None,
        "qty": item.quantity,
        "price": None,  # arcana_inventory has no price column
        "note": item.note or None,
        "expires": item.expires_at or None,
    }


async def _fetch_arcana_inventory(user_notion_id: str) -> list:
    try:
        items = await _arcana_inv_repo.get_list(
            category=None, status=None, user_notion_id=user_notion_id
        )
        # get_list excludes archived but may include barter; show all non-archived
        return [i for i in items if i.list_type == "инвентарь"]
    except Exception as e:
        logger.warning("arcana inventory PG query failed: %s", e)
        return []


@router.get("/arcana/inventory")
async def list_inventory(
    tg_id: int = Depends(current_user_id),
    cat: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    pg_items = await _fetch_arcana_inventory(user_notion_id)
    items: list[dict] = []
    counts: dict[str, int] = {c: 0 for c in ARCANA_INV_CATEGORIES}
    needle = (q or "").lower().strip()
    for inv_item in pg_items:
        it = _serialize_pg(inv_item)
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
    pg_items = await _fetch_arcana_inventory(user_notion_id)
    counts: dict[str, int] = {c: 0 for c in ARCANA_INV_CATEGORIES}
    for inv_item in pg_items:
        it = _serialize_pg(inv_item)
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


async def _ensure_owned(item_id: str, user_notion_id: str) -> InventoryItem:
    """Подгрузить item из PG + проверить что это инвентарь этого юзера."""
    item = await _arcana_inv_repo.get_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    if item.list_type != "инвентарь":
        raise HTTPException(status_code=404, detail="not an inventory item")
    # допускаем legacy items без owner
    if user_notion_id and item.user_notion_id and item.user_notion_id != user_notion_id:
        raise HTTPException(status_code=404, detail="not found")
    return item


@router.patch("/arcana/inventory/{item_id}")
async def edit_inventory(
    item_id: str,
    body: InventoryEditBody,
    tg_id: int = Depends(current_user_id),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    await _ensure_owned(item_id, user_notion_id)
    fields: dict = {}
    if body.qty is not None:
        fields["quantity"] = float(body.qty)
    if body.note is not None:
        fields["note"] = body.note
    if body.expires is not None:
        fields["expires_at"] = body.expires or None
    if not fields:
        return {"ok": True, "noop": True}
    await _arcana_inv_repo.update(item_id, **fields)
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
    inv_item = await _ensure_owned(item_id, user_notion_id)
    title = inv_item.name or "покупка"
    cat = inv_item.category or "💳 Прочее"
    finance_cat = CATEGORY_TO_FINANCE.get(cat, "🕯️ Расходники")
    fin_id = await _fin_repo.add(
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
        cur = float(inv_item.quantity or 0)
        await _arcana_inv_repo.update(item_id, quantity=cur + float(body.qty_added))
    from html import escape as _esc
    await notify_user(
        tg_id,
        f"🛒 Купила: <b>{_esc(title)}</b> — {int(round(body.price))}₽",
        bot="arcana",
    )
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
    inv_item = await _ensure_owned(item_id, user_notion_id)
    await _arcana_inv_repo.update_status(item_id, "Archived")
    title = inv_item.name or ""
    buy_id = None
    if body.add_to_buy:
        from core.list_manager import add_items
        cat = inv_item.category or None
        if title:
            created = await add_items(
                [{"name": title, "category": cat}],
                "🛒 Покупки",
                BOT_ARCANA,
                user_notion_id,
            )
            if created:
                buy_id = created[0].get("id")
    from html import escape as _esc
    suffix = " → в 🛒 Покупки" if buy_id else ""
    await notify_user(
        tg_id,
        f"🌚 Закончилось: <b>{_esc(title or 'расходник')}</b>{suffix}",
        bot="arcana",
    )
    return {"ok": True, "archived": True, "buy_id": buy_id}
