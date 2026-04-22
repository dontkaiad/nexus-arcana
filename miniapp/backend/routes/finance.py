"""miniapp/backend/routes/finance.py — GET /api/finance."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id
from core.budget import (
    cat_link,
    display_limit_name,
    get_limits,
    load_budget_data,
)

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    cat_from_notion,
    select_name,
    title_text,
    today_user_tz,
)

logger = logging.getLogger("miniapp.finance")

router = APIRouter()

ALLOWED_VIEWS = {"today", "month", "limits", "goals"}


def _pct(spent: float, limit: float) -> int:
    return int(round(spent / limit * 100)) if limit > 0 else 0


def _zone(pct: int) -> str:
    if pct < 60:
        return "green"
    if pct <= 85:
        return "yellow"
    return "red"


def _month_bounds(month: str) -> tuple[str, str]:
    """'2026-04' → ('2026-04-01', '2026-05-01')."""
    y, m = int(month[:4]), int(month[5:7])
    start = f"{y}-{m:02d}-01"
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = f"{ny}-{nm:02d}-01"
    return start, end


async def _nexus_finance_records(
    user_notion_id: str,
    date_on_or_after: str,
    date_before: str,
    type_filter: Optional[str] = None,
) -> list[dict]:
    """Факт. финансовые записи Nexus в диапазоне [start, end)."""
    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": BOT_NEXUS}},
        {"property": "Дата", "date": {"on_or_after": date_on_or_after}},
        {"property": "Дата", "date": {"before": date_before}},
    ]
    if type_filter:
        conditions.append({"property": "Тип", "select": {"equals": type_filter}})
    if user_notion_id:
        conditions.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    return await query_pages(
        config.nexus.db_finance, filters={"and": conditions}, page_size=500,
    )


def _extract_finance_item(page: dict) -> dict:
    props = page.get("properties", {})
    amt = (props.get("Сумма", {}).get("number")) or 0
    type_name = select_name(props.get("Тип", {}))
    cat_full = select_name(props.get("Категория", {}))
    return {
        "id": page.get("id", ""),
        "desc": title_text(props.get("Описание", {})),
        "cat": cat_from_notion(cat_full),
        "amt": int(round(amt)),
        "type": "income" if "Доход" in type_name else "expense",
    }


# ── View: today ──────────────────────────────────────────────────────────────

async def _view_today(tg_id: int) -> dict:
    today_date, _ = await today_user_tz(tg_id)
    today_iso = today_date.isoformat()
    tomorrow_iso = (today_date + timedelta(days=1)).isoformat()
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    records = await _nexus_finance_records(user_notion_id, today_iso, tomorrow_iso,
                                           type_filter="💸 Расход")
    items = [_extract_finance_item(p) for p in records]
    total = sum(i["amt"] for i in items)
    return {
        "view": "today",
        "date": today_iso,
        "total": total,
        "items": items,
    }


# ── View: month ──────────────────────────────────────────────────────────────

async def _view_month(tg_id: int, month: str) -> dict:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    start, end = _month_bounds(month)

    records = await _nexus_finance_records(user_notion_id, start, end)
    income = 0.0
    expense = 0.0
    by_cat: dict[str, float] = {}
    for p in records:
        props = p.get("properties", {})
        amt = (props.get("Сумма", {}).get("number")) or 0
        type_name = select_name(props.get("Тип", {}))
        cat_full = select_name(props.get("Категория", {}))
        if "Доход" in type_name:
            income += amt
        elif "Расход" in type_name:
            expense += amt
            if cat_full:
                by_cat[cat_full] = by_cat.get(cat_full, 0) + amt

    limits_map = await get_limits()  # {cat_link: amount}

    by_category: list[dict] = []
    for cat_full, spent in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        limit = limits_map.get(cat_link(cat_full))
        item = {
            "cat": cat_from_notion(cat_full),
            "spent": int(round(spent)),
            "limit": int(round(limit)) if limit else None,
            "pct": _pct(spent, limit) if limit else None,
        }
        by_category.append(item)

    return {
        "view": "month",
        "month": month,
        "income": int(round(income)),
        "expense": int(round(expense)),
        "balance": int(round(income - expense)),
        "by_category": by_category,
    }


# ── View: limits ─────────────────────────────────────────────────────────────

async def _view_limits(tg_id: int, month: str) -> dict:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    start, end = _month_bounds(month)

    records = await _nexus_finance_records(user_notion_id, start, end,
                                           type_filter="💸 Расход")
    spent_by_link: dict[str, float] = {}
    for p in records:
        props = p.get("properties", {})
        amt = (props.get("Сумма", {}).get("number")) or 0
        cat_full = select_name(props.get("Категория", {}))
        if cat_full:
            link = cat_link(cat_full)
            spent_by_link[link] = spent_by_link.get(link, 0) + amt

    limits_map = await get_limits()

    categories: list[dict] = []
    for link, limit in limits_map.items():
        spent = spent_by_link.get(link, 0)
        pct = _pct(spent, limit)
        display = display_limit_name(link)
        categories.append({
            "cat": cat_from_notion(display),
            "spent": int(round(spent)),
            "limit": int(round(limit)),
            "pct": pct,
            "zone": _zone(pct),
        })
    categories.sort(key=lambda c: -c["pct"])

    return {
        "view": "limits",
        "month": month,
        "categories": categories,
    }


# ── View: goals ──────────────────────────────────────────────────────────────

def _serialize_debt(d: dict) -> dict:
    note = (d.get("strategy") or "").strip() or (d.get("fact") or "").strip()
    total = int(round(d.get("amount", 0)))
    return {
        "key": d.get("key") or "",
        "name": d.get("name", ""),
        "total": total,
        "left": total,  # пока отдельного поля нет — см. спеку
        "by": d.get("deadline") or None,
        "note": note or None,
    }


def _serialize_goal(g: dict) -> dict:
    return {
        "key": g.get("key") or "",
        "name": g.get("name", ""),
        "target": int(round(g.get("target", 0))),
        "saved": 0,
        "monthly": int(round(g.get("saving", 0))),
        "after": None,
    }


async def _view_goals(tg_id: int) -> dict:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    data = await load_budget_data(user_notion_id)
    return {
        "view": "goals",
        "debts": [_serialize_debt(d) for d in data.get("долги", [])],
        "goals": [_serialize_goal(g) for g in data.get("цели", [])],
    }


# ── Router ───────────────────────────────────────────────────────────────────

@router.get("/finance")
async def get_finance(
    tg_id: int = Depends(current_user_id),
    view: str = Query("today", description="today|month|limits|goals"),
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> dict[str, Any]:
    if view not in ALLOWED_VIEWS:
        raise HTTPException(status_code=400, detail=f"view must be one of {sorted(ALLOWED_VIEWS)}")

    if not month:
        today_date, _ = await today_user_tz(tg_id)
        month = today_date.strftime("%Y-%m")

    if view == "today":
        return await _view_today(tg_id)
    if view == "month":
        return await _view_month(tg_id, month)
    if view == "limits":
        return await _view_limits(tg_id, month)
    return await _view_goals(tg_id)


@router.get("/finance/category")
async def get_finance_category(
    tg_id: int = Depends(current_user_id),
    cat: str = Query(..., description="Полное имя категории (с emoji), например '🏠 Жилье'"),
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> dict[str, Any]:
    """Wave5.9: drill-down — все траты по категории за месяц."""
    if not month:
        today_date, _ = await today_user_tz(tg_id)
        month = today_date.strftime("%Y-%m")

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    start_iso, end_iso = _month_bounds(month)

    conditions: list[dict] = [
        {"property": "Бот", "select": {"equals": BOT_NEXUS}},
        {"property": "Тип", "select": {"equals": "💸 Расход"}},
        {"property": "Категория", "select": {"equals": cat}},
        {"property": "Дата", "date": {"on_or_after": start_iso}},
        {"property": "Дата", "date": {"before": end_iso}},
    ]
    if user_notion_id:
        conditions.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })

    try:
        pages = await query_pages(
            config.nexus.db_finance,
            filters={"and": conditions},
            sorts=[{"property": "Дата", "direction": "descending"}],
            page_size=200,
        )
    except Exception as e:
        logger.warning("finance/category query failed: %s", e)
        pages = []

    items: list[dict] = []
    total = 0.0
    for p in pages:
        props = p.get("properties", {})
        amount = (props.get("Сумма", {}).get("number")) or 0
        desc = title_text(props.get("Описание", {}))
        date_raw = (props.get("Дата", {}).get("date") or {}).get("start") or ""
        items.append({
            "id": p.get("id", ""),
            "amount": amount,
            "desc": desc,
            "date": date_raw[:10],
        })
        total += amount

    return {
        "cat": cat,
        "month": month,
        "total": int(round(total)),
        "count": len(items),
        "items": items,
    }
