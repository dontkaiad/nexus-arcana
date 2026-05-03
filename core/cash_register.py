"""core/cash_register.py — касса Арканы и P&L.

Касса = (Σ Оплачено по Раскладам+Ритуалам кроме self-client)
       − (расходы Финансы Бот=Arcana)
       − (выплаты себе: Финансы Бот=Nexus, Тип=Доход, Категория=💰 Зарплата)

Self-client (🌟 Self) всегда исключён из дохода кассы.
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Optional, List

from core.config import config
from core.notion_client import (
    _with_user_filter,
    query_pages,
    rituals_all,
    sessions_all,
)

logger = logging.getLogger("core.cash_register")

SALARY_CATEGORY = "💰 Зарплата"
BARTER_CATEGORY = "🔄 Бартер"
BOT_ARCANA = "🌒 Arcana"
BOT_NEXUS = "☀️ Nexus"


def _num(prop: dict) -> float:
    return float((prop or {}).get("number") or 0)


def _select_name(prop: dict) -> str:
    return ((prop or {}).get("select") or {}).get("name", "") or ""


def _date_start(prop: dict) -> str:
    return ((prop or {}).get("date") or {}).get("start", "") or ""


def _in_month(iso: str, year: int, month: int) -> bool:
    if not iso:
        return False
    return iso[:7] == f"{year}-{month:02d}"


def _client_type(client_pages_by_id: dict, client_rel_ids: List[str]) -> str:
    if not client_rel_ids:
        return ""
    cid = client_rel_ids[0]
    page = client_pages_by_id.get(cid) or {}
    sel = (page.get("properties", {}).get("Тип клиента", {}) or {}).get("select") or {}
    return sel.get("name", "")


async def _load_clients_map(user_notion_id: str) -> dict:
    """{page_id: page} — для быстрого определения 🌟 Self."""
    db_id = config.arcana.db_clients
    if not db_id:
        return {}
    filters = _with_user_filter(None, user_notion_id)
    pages = await query_pages(db_id, filters=filters, page_size=500)
    return {p.get("id", ""): p for p in pages}


async def _load_arcana_finance(
    user_notion_id: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> List[dict]:
    """Финансы практики (Бот=🌒 Arcana). period фильтруется в Python — нужны
    как месячные, так и lifetime данные."""
    from core.notion_client import arcana_finance_summary
    return await arcana_finance_summary(user_notion_id, month, year)


async def _load_salary_records(user_notion_id: str) -> List[dict]:
    """Финансы Бот=Nexus, Тип=Доход, Категория=Зарплата (lifetime)."""
    db_id = config.nexus.db_finance
    if not db_id:
        return []
    base = {
        "and": [
            {"property": "Бот", "select": {"equals": BOT_NEXUS}},
            {"property": "Тип", "select": {"equals": "💰 Доход"}},
            {"property": "Категория", "select": {"equals": SALARY_CATEGORY}},
        ]
    }
    filters = _with_user_filter(base, user_notion_id)
    return await query_pages(db_id, filters=filters, page_size=500)


def _filter_non_self(pages: List[dict], clients_map: dict) -> List[dict]:
    """Убрать страницы, где клиент — 🌟 Self."""
    out: List[dict] = []
    for p in pages:
        rels = (p.get("properties", {}).get("👥 Клиенты", {}) or {}).get("relation") or []
        ids = [r.get("id", "") for r in rels if r.get("id")]
        if ids and _client_type(clients_map, ids) == "🌟 Self":
            continue
        out.append(p)
    return out


def _sum_paid(pages: List[dict]) -> float:
    return sum(_num(p.get("properties", {}).get("Оплачено", {})) for p in pages)


def _sum_price(pages: List[dict], price_field: str) -> float:
    return sum(_num(p.get("properties", {}).get(price_field, {})) for p in pages)


async def _count_open_barter(user_notion_id: str) -> int:
    """Не-Done пункты чеклиста с категорией 🔄 Бартер в 🗒️ Списки."""
    db_id = config.db_lists
    if not db_id:
        return 0
    base = {
        "and": [
            {"property": "Тип", "select": {"equals": "📋 Чеклист"}},
            {"property": "Категория", "select": {"equals": BARTER_CATEGORY}},
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Archived"}},
        ]
    }
    filters = _with_user_filter(base, user_notion_id)
    try:
        pages = await query_pages(db_id, filters=filters, page_size=500)
        return len(pages)
    except Exception as e:
        logger.warning("count_open_barter failed: %s", e)
        return 0


async def compute_pnl(
    user_notion_id: str,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> dict:
    """Полный P&L и состояние кассы.

    Период (year+month) — для income/expenses_month/profit_month.
    cash_balance — lifetime.
    """
    if not year or not month:
        today = _date.today()
        year, month = today.year, today.month

    clients_map = await _load_clients_map(user_notion_id)
    sessions = await sessions_all(user_notion_id=user_notion_id)
    rituals = await rituals_all(user_notion_id=user_notion_id)
    sessions_paying = _filter_non_self(sessions, clients_map)
    rituals_paying = _filter_non_self(rituals, clients_map)

    # ── Доход за месяц (по полю Дата сессии/ритуала) ──────────────────────
    sessions_month = [
        p for p in sessions_paying
        if _in_month(_date_start(p.get("properties", {}).get("Дата", {})), year, month)
    ]
    rituals_month = [
        p for p in rituals_paying
        if _in_month(_date_start(p.get("properties", {}).get("Дата", {})), year, month)
    ]
    income_sessions = _sum_paid(sessions_month)
    income_rituals = _sum_paid(rituals_month)
    income_month_total = income_sessions + income_rituals

    # ── Расходы Arcana за месяц ──────────────────────────────────────────
    finance_lifetime = await _load_arcana_finance(user_notion_id)
    expenses_month_by_cat: dict[str, float] = {}
    expenses_month_total = 0.0
    for r in finance_lifetime:
        props = r.get("properties", {})
        if "Доход" in _select_name(props.get("Тип", {})):
            continue
        if not _in_month(_date_start(props.get("Дата", {})), year, month):
            continue
        amt = _num(props.get("Сумма", {}))
        cat = _select_name(props.get("Категория", {})) or "💳 Прочее"
        expenses_month_total += amt
        expenses_month_by_cat[cat] = expenses_month_by_cat.get(cat, 0.0) + amt

    profit_month = income_month_total - expenses_month_total

    # ── Касса (lifetime) ─────────────────────────────────────────────────
    income_lifetime = _sum_paid(sessions_paying) + _sum_paid(rituals_paying)
    expenses_lifetime = sum(
        _num(r.get("properties", {}).get("Сумма", {}))
        for r in finance_lifetime
        if "Доход" not in _select_name(r.get("properties", {}).get("Тип", {}))
    )
    salary_records = await _load_salary_records(user_notion_id)
    salary_lifetime = sum(_num(r.get("properties", {}).get("Сумма", {})) for r in salary_records)
    salary_month = sum(
        _num(r.get("properties", {}).get("Сумма", {}))
        for r in salary_records
        if _in_month(_date_start(r.get("properties", {}).get("Дата", {})), year, month)
    )
    cash_balance = income_lifetime - expenses_lifetime - salary_lifetime

    # ── Долги клиентов ──────────────────────────────────────────────────
    debt_money = 0.0
    for p in sessions_paying:
        price = _num(p.get("properties", {}).get("Сумма", {}))
        paid = _num(p.get("properties", {}).get("Оплачено", {}))
        debt_money += max(0.0, price - paid)
    for p in rituals_paying:
        price = _num(p.get("properties", {}).get("Цена за ритуал", {}))
        paid = _num(p.get("properties", {}).get("Оплачено", {}))
        debt_money += max(0.0, price - paid)

    barter_open = await _count_open_barter(user_notion_id)

    return {
        "period": {"year": year, "month": month},
        "income_month": int(round(income_month_total)),
        "income_breakdown": {
            "sessions": {"amount": int(round(income_sessions)), "count": len(sessions_month)},
            "rituals": {"amount": int(round(income_rituals)), "count": len(rituals_month)},
        },
        "expenses_month": int(round(expenses_month_total)),
        "expenses_by_category": [
            {"name": k, "amount": int(round(v))}
            for k, v in sorted(expenses_month_by_cat.items(), key=lambda x: -x[1])
        ],
        "profit_month": int(round(profit_month)),
        "salary_month": int(round(salary_month)),
        "salary_lifetime": int(round(salary_lifetime)),
        "cash_balance": int(round(cash_balance)),
        "debt_money": int(round(debt_money)),
        "barter_open_count": barter_open,
    }
