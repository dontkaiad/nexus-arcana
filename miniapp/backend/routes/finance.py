"""miniapp/backend/routes/finance.py — GET /api/finance."""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.user_manager import get_user_notion_id
from core.budget import (
    GOAL_RE,
    budget_day_limit_from_plan,
    cat_link,
    display_limit_name,
    get_limits,
    load_budget_data,
    parse_amount,
)
from core.repos.pg_finance_repo import BudgetEntry, PgNexusBudgetRepo
from core.repos.pg_memory_repo import PgMemoryRepo, Memory

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    cat_from_notion,
    today_user_tz,
)

logger = logging.getLogger("miniapp.finance")

router = APIRouter()

_budget_repo = PgNexusBudgetRepo()
_mem_repo = PgMemoryRepo()

ALLOWED_VIEWS = {"today", "month", "limits", "goals"}


def _pct(spent: float, limit: float) -> int:
    return int(round(spent / limit * 100)) if limit > 0 else 0


def _zone(pct: int) -> str:
    if pct < 60:
        return "green"
    if pct <= 85:
        return "yellow"
    return "red"


def _month_bounds(month: str) -> tuple:
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
) -> List[BudgetEntry]:
    """Факт. финансовые записи Nexus в диапазоне [start, end)."""
    try:
        return await _budget_repo.query(
            date_from=date_on_or_after,
            date_to=date_before,
            type_=type_filter or None,
            page_size=500,
            user_notion_id=user_notion_id,
        )
    except Exception as e:
        logger.warning("_nexus_finance_records PG query failed: %s", e)
        return []


def _extract_finance_item(entry: BudgetEntry) -> dict:
    return {
        "id": entry.id,
        "desc": entry.description,
        "cat": cat_from_notion(entry.category),
        "amt": int(round(entry.amount)),
        "type": "income" if "Доход" in entry.type_ else "expense",
    }


# ── View: today ──────────────────────────────────────────────────────────────

async def _view_today(tg_id: int) -> dict:
    today_date, _ = await today_user_tz(tg_id)
    today_iso = today_date.isoformat()
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    records = await _nexus_finance_records(user_notion_id, today_iso, today_iso,
                                           type_filter="💸 Расход")
    items = [_extract_finance_item(e) for e in records]
    total = sum(i["amt"] for i in items)

    budget_day = await budget_day_limit_from_plan(user_notion_id)
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
    by_cat: dict = {}
    for entry in records:
        if "Доход" in entry.type_:
            income += entry.amount
        elif "Расход" in entry.type_:
            expense += entry.amount
            if entry.category:
                by_cat[entry.category] = by_cat.get(entry.category, 0) + entry.amount

    limits_map = await get_limits()  # {cat_link: amount}

    by_category: List[dict] = []
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
    spent_by_link: dict = {}
    for entry in records:
        if entry.category:
            link = cat_link(entry.category)
            spent_by_link[link] = spent_by_link.get(link, 0) + entry.amount

    limits_map = await get_limits()

    categories: List[dict] = []
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


def _debt_schedule(amount: float, monthly_payment: float, today_d: date) -> List[dict]:
    """[{'month': 'май 2026', 'amount': 20000}, ...] — план выплат от текущего месяца.

    Если monthly_payment == 0 → [] (долг отложен).
    Кап: 60 платежей (5 лет), чтобы случайные кривые данные не разнесли ответ.
    """
    if monthly_payment <= 0 or amount <= 0:
        return []
    schedule: List[dict] = []
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


def _all_debts_close_label(debts_serialized: List[dict]) -> Optional[str]:
    """Самая поздняя дата окончания платежей среди всех долгов с графиком."""
    months_order: List[tuple] = []  # (year, month_idx, label)
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


async def _find_debt_taken_dates(user_notion_id: str, today_d: date) -> dict:
    """Ищет в финансах доходы со словом «долг» в описании за последние 5 лет."""
    start = date(today_d.year - 5, 1, 1).isoformat()
    end = date(today_d.year + 1, 1, 1).isoformat()
    try:
        records = await _nexus_finance_records(user_notion_id, start, end)
    except Exception as e:
        logger.warning("debt taken-dates query failed: %s", e)
        return {}
    found: dict = {}
    for entry in records:
        if "Доход" not in entry.type_:
            continue
        desc = (entry.description or "").lower()
        if "долг" not in desc:
            continue
        if not entry.date:
            continue
        prev = found.get(desc)
        if prev is None or entry.date < prev:
            found[desc] = entry.date[:10]
    return found


def _match_taken_date(debt_name: str, taken_map: dict) -> Optional[str]:
    """Подбирает дату взятия по вхождению имени контрагента в описание."""
    needle = (debt_name or "").strip().lower()
    if not needle:
        return None
    best: Optional[str] = None
    for desc, iso in taken_map.items():
        if needle in desc and (best is None or iso < best):
            best = iso
    return best


async def _load_desc_synonyms(user_notion_id: str) -> dict:
    """wave8.64: из записей памяти «🛒 Предпочтения» строит карту синоним→канон."""
    try:
        mems = await _mem_repo.find_by_category(
            "🛒 Предпочтения",
            is_current=True,
            user_notion_id=user_notion_id,
            page_size=200,
        )
    except Exception as e:
        logger.warning("desc synonyms query failed: %s", e)
        return {}
    syn: dict = {}
    for mem in mems:
        fact = mem.fact or ""
        if "=" not in fact:
            continue
        left, right = fact.split("=", 1)
        canonical = re.split(r"[,.;]", right, maxsplit=1)[0].strip().lower()
        if not canonical:
            continue
        aliases = [a.strip().lower() for a in re.split(r"[\/,]", left) if a.strip()]
        for a in aliases:
            syn[a] = canonical
        syn.setdefault(canonical, canonical)
    return syn


async def _load_closed_budget(user_notion_id: str) -> dict:
    """Закрытые цели (Memory is_current=False) + закрытые долги (debts.is_active=False)."""
    out: dict = {"долги": [], "цели": []}

    # Закрытые цели — Memory
    try:
        all_closed = await _mem_repo.find_by_category(
            "",
            is_current=False,
            user_notion_id=user_notion_id,
            page_size=200,
        )
        for mem in all_closed:
            key = (mem.key or "").strip().lower()
            if not key.startswith("цель_"):
                continue
            fact = mem.fact or ""
            m = GOAL_RE.search(fact)
            if not m:
                continue
            saving = parse_amount(m.group(3)) if m.group(3) else 0
            closed_at = (mem.date or "")[:10] or None
            out["цели"].append({
                "key": key,
                "name": m.group(1).strip(),
                "target": int(round(parse_amount(m.group(2)))),
                "saved": int(round(parse_amount(m.group(2)))),
                "monthly": int(round(saving)),
                "closed_at": closed_at,
            })
    except Exception as e:
        logger.warning("closed budget goals query failed: %s", e)

    # Закрытые долги — debts table
    try:
        from core.repos.pg_debts_repo import _repo as _debt_repo
        closed_debts = await _debt_repo.list_closed(user_notion_id, kind="i_owe")
        for d in closed_debts:
            closed_at = d.updated_at[:10] if d.updated_at else None
            out["долги"].append({
                "key": "долг_{}".format(d.name.lower().replace(" ", "_")),
                "name": d.name,
                "total": int(round(d.amount)),
                "left": 0,
                "monthly_payment": int(round(d.monthly_payment or 0)),
                "note": d.strategy or "",
                "closed_at": closed_at,
            })
    except Exception as e:
        logger.warning("closed budget debts query failed: %s", e)

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
) -> dict:
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
    cat: str = Query(..., description="Полное имя категории (с emoji), например '🏠 Жильё'"),
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> dict:
    """Wave5.9: drill-down — все траты по категории за месяц."""
    if not month:
        today_date, _ = await today_user_tz(tg_id)
        month = today_date.strftime("%Y-%m")

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    start_iso, end_iso = _month_bounds(month)

    try:
        records = await _nexus_finance_records(user_notion_id, start_iso, end_iso)
    except Exception as e:
        logger.warning("finance/category query failed: %s", e)
        records = []

    synonyms = await _load_desc_synonyms(user_notion_id)

    items: List[dict] = []
    total = 0.0
    by_desc: dict = {}
    for entry in records:
        if "Расход" not in entry.type_:
            continue
        if entry.category != cat:
            continue
        items.append({
            "id": entry.id,
            "amount": entry.amount,
            "desc": entry.description,
            "date": entry.date[:10] if entry.date else "",
        })
        total += entry.amount
        raw_key = (entry.description or "—").strip().lower()
        key = synonyms.get(raw_key, raw_key)
        by_desc[key] = by_desc.get(key, 0) + entry.amount

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
