"""miniapp/backend/routes/finance.py — GET /api/finance."""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id
from core.budget import (
    DEBT_RE,
    GOAL_RE,
    cat_link,
    display_limit_name,
    get_limits,
    load_budget_data,
    parse_amount,
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

_DEFAULT_BUDGET_DAY = 4166


async def _budget_day_limit() -> int:
    from core.notion_client import memory_get
    raw = await memory_get("budget_day_limit")
    if raw:
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            pass
    return _DEFAULT_BUDGET_DAY


async def _view_today(tg_id: int) -> dict:
    today_date, _ = await today_user_tz(tg_id)
    today_iso = today_date.isoformat()
    tomorrow_iso = (today_date + timedelta(days=1)).isoformat()
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    records = await _nexus_finance_records(user_notion_id, today_iso, tomorrow_iso,
                                           type_filter="💸 Расход")
    items = [_extract_finance_item(p) for p in records]
    total = sum(i["amt"] for i in items)

    # wave6.1.3: блок бюджета дня (как в /api/today) — пропал в волне 5
    budget_day = await _budget_day_limit()
    left = max(0, budget_day - total)
    pct = _pct(total, budget_day)
    return {
        "view": "today",
        "date": today_iso,
        "total": total,
        "items": items,
        "budget": {
            "day": budget_day,
            "spent": total,
            "left": left,
            "pct": pct,
        },
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

_RU_MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}
_RU_MONTHS_NOM = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь",
    7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def _debt_schedule(amount: float, monthly_payment: float, today_d: date) -> list[dict]:
    """[{'month': 'май 2026', 'amount': 20000}, ...] — план выплат от текущего месяца.

    Если monthly_payment == 0 → [] (долг отложен).
    Кап: 60 платежей (5 лет), чтобы случайные кривые данные не разнесли ответ.
    """
    if monthly_payment <= 0 or amount <= 0:
        return []
    schedule: list[dict] = []
    remaining = amount
    cur = date(today_d.year, today_d.month, 1)
    while remaining > 0 and len(schedule) < 60:
        pay = min(remaining, monthly_payment)
        schedule.append({
            "month": f"{_RU_MONTHS_NOM[cur.month]} {cur.year}",
            "amount": int(round(pay)),
        })
        remaining -= pay
        cur = _add_months(cur, 1)
    return schedule


_MONTHLY_FALLBACK_RE = re.compile(
    r'(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]\s*/\s*мес', re.IGNORECASE
)


def _extract_monthly_fallback(*texts: str) -> float:
    """Из «20 000₽/мес — с мая 2026» вытаскивает 20000."""
    for t in texts:
        if not t:
            continue
        m = _MONTHLY_FALLBACK_RE.search(t)
        if m:
            try:
                return parse_amount(m.group(1))
            except ValueError:
                continue
    return 0


def _serialize_debt(d: dict, today_d: date) -> dict:
    note = (d.get("strategy") or "").strip() or (d.get("fact") or "").strip()
    total = int(round(d.get("amount", 0)))
    monthly = int(round(d.get("monthly_payment", 0)))
    if monthly <= 0:
        monthly = int(round(_extract_monthly_fallback(
            d.get("strategy") or "", d.get("fact") or "")))
    schedule = _debt_schedule(total, monthly, today_d)
    return {
        "key": d.get("key") or "",
        "name": d.get("name", ""),
        "total": total,
        "left": total,  # отдельного "left" пока нет — см. спеку
        "by": d.get("deadline") or None,
        "note": note or None,
        "monthly_payment": monthly,
        "schedule": schedule,
        "ends": schedule[-1]["month"] if schedule else None,
    }


def _all_debts_close_label(debts_serialized: list[dict]) -> Optional[str]:
    """Самая поздняя дата окончания платежей среди всех долгов с графиком."""
    months_order: list[tuple[int, int, str]] = []  # (year, month_idx, label)
    name_to_idx = {v: k for k, v in _RU_MONTHS_NOM.items()}
    for d in debts_serialized:
        if not d.get("ends"):
            continue
        try:
            mname, year_str = d["ends"].rsplit(" ", 1)
            months_order.append((int(year_str), name_to_idx[mname], d["ends"]))
        except (ValueError, KeyError):
            continue
    if not months_order:
        return None
    months_order.sort()
    return months_order[-1][2]


def _serialize_goal(g: dict, today_d: date, all_debts_close: Optional[str]) -> dict:
    target = int(round(g.get("target", 0)))
    monthly = int(round(g.get("saving", 0)))
    after: Optional[str] = None
    if monthly > 0 and target > 0:
        months_to = max(1, -(-target // monthly))  # ceil
        eta = _add_months(date(today_d.year, today_d.month, 1), months_to - 1)
        after = f"~{_RU_MONTHS[eta.month]} {eta.year}"
    elif all_debts_close:
        after = f"закрытия долгов (~{all_debts_close})"
    else:
        after = "закрытия долгов"
    return {
        "key": g.get("key") or "",
        "name": g.get("name", ""),
        "target": target,
        "saved": 0,
        "monthly": monthly,
        "after": after,
    }


async def _find_debt_taken_dates(user_notion_id: str, today_d: date) -> dict[str, str]:
    """Ищет в финансах доходы со словом «долг» в описании за последние 5 лет.
    Возвращает {имя_контрагента_lowercase: ISO-дата самой ранней такой записи}.
    """
    start = date(today_d.year - 5, 1, 1).isoformat()
    end = date(today_d.year + 1, 1, 1).isoformat()
    try:
        records = await _nexus_finance_records(user_notion_id, start, end)
    except Exception as e:
        logger.warning("debt taken-dates query failed: %s", e)
        return {}
    found: dict[str, str] = {}
    for p in records:
        props = p.get("properties", {})
        type_name = select_name(props.get("Тип", {}))
        if "Доход" not in type_name:
            continue
        desc = (title_text(props.get("Описание", {})) or "").lower()
        if "долг" not in desc:
            continue
        date_raw = (props.get("Дата", {}).get("date") or {}).get("start") or ""
        if not date_raw:
            continue
        # выбираем самую раннюю — это и есть дата взятия
        prev = found.get(desc)
        if prev is None or date_raw < prev:
            found[desc] = date_raw[:10]
    return found


def _match_taken_date(debt_name: str, taken_map: dict[str, str]) -> Optional[str]:
    """Подбирает дату взятия по вхождению имени контрагента в описание."""
    needle = (debt_name or "").strip().lower()
    if not needle:
        return None
    best: Optional[str] = None
    for desc, iso in taken_map.items():
        if needle in desc and (best is None or iso < best):
            best = iso
    return best


async def _load_closed_budget(user_notion_id: str) -> dict[str, list[dict]]:
    """Закрытые долги/цели (Актуально == false). closed_at = last_edited_time[:10]."""
    from core.notion_client import db_query
    from core.config import config
    mem_db = os.environ.get("NOTION_DB_MEMORY") or config.nexus.db_memory
    out: dict[str, list[dict]] = {"долги": [], "цели": []}
    if not mem_db:
        return out
    key_filter = {"or": [
        {"property": "Ключ", "rich_text": {"starts_with": "цель_"}},
        {"property": "Ключ", "rich_text": {"starts_with": "долг_"}},
    ]}
    conditions: list[dict] = [
        key_filter,
        {"property": "Актуально", "checkbox": {"equals": False}},
    ]
    if user_notion_id:
        conditions.append({"property": "🪪 Пользователи",
                           "relation": {"contains": user_notion_id}})
    try:
        pages = await db_query(mem_db, filter_obj={"and": conditions}, page_size=200)
    except Exception as e:
        logger.warning("closed budget query failed: %s", e)
        return out
    for p in pages:
        props = p.get("properties", {})
        fact_parts = props.get("Текст", {}).get("title", [])
        fact = fact_parts[0]["plain_text"] if fact_parts else ""
        key_parts = props.get("Ключ", {}).get("rich_text", [])
        key = key_parts[0]["plain_text"].strip().lower() if key_parts else ""
        closed_at = (p.get("last_edited_time") or "")[:10] or None
        if key.startswith("цель_"):
            m = GOAL_RE.search(fact)
            if not m:
                continue
            saving = parse_amount(m.group(3)) if m.group(3) else 0
            out["цели"].append({
                "key": key,
                "name": m.group(1).strip(),
                "target": int(round(parse_amount(m.group(2)))),
                "saved": int(round(parse_amount(m.group(2)))),
                "monthly": int(round(saving)),
                "closed_at": closed_at,
            })
        elif key.startswith("долг_"):
            m = DEBT_RE.search(fact)
            if not m:
                continue
            mp_raw = (m.group(5) or "").strip()
            monthly = parse_amount(mp_raw) if mp_raw else 0
            if monthly <= 0:
                monthly = _extract_monthly_fallback(m.group(4) or "", fact)
            out["долги"].append({
                "key": key,
                "name": m.group(1).strip(),
                "total": int(round(parse_amount(m.group(2)))),
                "left": 0,
                "monthly_payment": int(round(monthly)),
                "note": (m.group(4) or "").strip() or fact,
                "closed_at": closed_at,
            })
    return out


async def _view_goals(tg_id: int) -> dict:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    data = await load_budget_data(user_notion_id)
    today_d, _ = await today_user_tz(tg_id)
    taken_map = await _find_debt_taken_dates(user_notion_id, today_d)
    debts_ser = []
    for d in data.get("долги", []):
        ser = _serialize_debt(d, today_d)
        ser["taken_at"] = _match_taken_date(ser["name"], taken_map)
        debts_ser.append(ser)
    all_close = _all_debts_close_label(debts_ser)
    goals_ser = [_serialize_goal(g, today_d, all_close) for g in data.get("цели", [])]
    closed = await _load_closed_budget(user_notion_id)
    closed["долги"].sort(key=lambda x: x.get("closed_at") or "", reverse=True)
    closed["цели"].sort(key=lambda x: x.get("closed_at") or "", reverse=True)
    return {
        "view": "goals",
        "debts": debts_ser,
        "goals": goals_ser,
        "debts_close_at": all_close,
        "closed_debts": closed["долги"],
        "closed_goals": closed["цели"],
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

    # wave8.50: используем общий загрузчик и фильтруем тип/категорию в Python —
    # в Notion-select встречаются варианты "💸 Расход"/"Расход"/"💸 Покупка",
    # из-за жёсткого equals-фильтра drill-down ловил пустоту, хотя сводка месяца
    # такие траты считала (там match по подстроке "Расход").
    try:
        pages = await _nexus_finance_records(user_notion_id, start_iso, end_iso)
    except Exception as e:
        logger.warning("finance/category query failed: %s", e)
        pages = []

    items: list[dict] = []
    total = 0.0
    by_desc: dict[str, float] = {}
    for p in pages:
        props = p.get("properties", {})
        type_name = select_name(props.get("Тип", {}))
        if "Расход" not in type_name:
            continue
        cat_full = select_name(props.get("Категория", {}))
        if cat_full != cat:
            continue
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
        key = (desc or "—").strip().lower()
        by_desc[key] = by_desc.get(key, 0) + amount

    items.sort(key=lambda x: x["date"], reverse=True)

    by_desc_list = [
        {"name": name, "amount": int(round(amt))}
        for name, amt in sorted(by_desc.items(), key=lambda kv: -kv[1])
    ]

    return {
        "cat": cat,
        "month": month,
        "total": int(round(total)),
        "count": len(items),
        "items": items,
        "by_desc": by_desc_list,
    }
