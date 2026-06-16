"""core/cash_register.py — касса Арканы и P&L.

Касса = (Σ Оплачено по Раскладам+Ритуалам кроме self-client)
       − (расходы arcana_pnl)
       − (выплаты себе: nexus_budget, Тип=💰 Доход, Категория=💰 Зарплата)

Self-client (type_code="self") всегда исключён из дохода кассы.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Optional, List, Set

logger = logging.getLogger("core.cash_register")

SALARY_CATEGORY = "💰 Зарплата"
BARTER_CATEGORY = "🔄 Бартер"
BOT_ARCANA = "🌒 Arcana"
BOT_NEXUS = "☀️ Nexus"

_EPOCH = "2020-01-01"
_FUTURE = "2099-12-31"


async def _load_clients(user_notion_id: str) -> List:
    from arcana.repos.pg_clients_repo import PgClientsRepo
    return await PgClientsRepo().list_all(user_notion_id)


async def _load_sessions(user_notion_id: str) -> List:
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    return await PgSessionsRepo().list_all(user_notion_id=user_notion_id)


async def _load_rituals(user_notion_id: str) -> List:
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    return await PgRitualsRepo().list_all(user_notion_id=user_notion_id)


async def _load_arcana_finance(user_notion_id: str) -> List:
    from core.repos.pg_finance_repo import PgArcanaPnlRepo
    return await PgArcanaPnlRepo().query(
        _EPOCH, _FUTURE,
        page_size=2000,
        user_notion_id=user_notion_id,
    )


async def _load_salary_records(user_notion_id: str) -> List:
    from core.repos.pg_finance_repo import PgNexusBudgetRepo
    return await PgNexusBudgetRepo().query(
        _EPOCH, _FUTURE,
        type_="💰 Доход",
        category=SALARY_CATEGORY,
        page_size=1000,
        user_notion_id=user_notion_id,
    )


async def _count_open_barter(user_notion_id: str) -> int:
    from core.repos.pg_nexus_lists_repo import PgArcanaInventoryRepo
    items = await PgArcanaInventoryRepo().get_open_barter(user_notion_id)
    return len(items)


def _self_client_ids(clients) -> Set[str]:
    return {c.id for c in clients if c.type_code == "self"}


def _in_month_entry(date_val, year: int, month: int) -> bool:
    if not date_val:
        return False
    if isinstance(date_val, datetime):
        return date_val.year == year and date_val.month == month
    # str "YYYY-MM-DD"
    return str(date_val)[:7] == f"{year}-{month:02d}"


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

    clients = await _load_clients(user_notion_id)
    self_ids = _self_client_ids(clients)

    sessions = await _load_sessions(user_notion_id)
    rituals = await _load_rituals(user_notion_id)

    sessions_paying = [s for s in sessions if s.client_id not in self_ids]
    rituals_paying = [r for r in rituals if r.client_id not in self_ids]

    # ── Доход за месяц ────────────────────────────────────────────────────────
    sessions_month = [s for s in sessions_paying if _in_month_entry(s.date, year, month)]
    rituals_month = [r for r in rituals_paying if _in_month_entry(r.date, year, month)]
    income_sessions = sum(float(s.paid) for s in sessions_month)
    income_rituals = sum(float(r.paid) for r in rituals_month)
    income_month_total = income_sessions + income_rituals

    # ── Расходы Arcana за месяц ───────────────────────────────────────────────
    finance_lifetime = await _load_arcana_finance(user_notion_id)
    expenses_month_by_cat: dict = {}
    expenses_month_total = 0.0
    for rec in finance_lifetime:
        if "Доход" in (rec.type_ or ""):
            continue
        if not _in_month_entry(rec.date, year, month):
            continue
        amt = float(rec.amount)
        cat = rec.category or "💳 Прочее"
        expenses_month_total += amt
        expenses_month_by_cat[cat] = expenses_month_by_cat.get(cat, 0.0) + amt

    profit_month = income_month_total - expenses_month_total

    # ── Касса (lifetime) ──────────────────────────────────────────────────────
    income_lifetime = (
        sum(float(s.paid) for s in sessions_paying)
        + sum(float(r.paid) for r in rituals_paying)
    )
    expenses_lifetime = sum(
        float(rec.amount) for rec in finance_lifetime
        if "Доход" not in (rec.type_ or "")
    )
    salary_records = await _load_salary_records(user_notion_id)
    salary_lifetime = sum(float(r.amount) for r in salary_records)
    salary_month = sum(
        float(r.amount) for r in salary_records
        if _in_month_entry(r.date, year, month)
    )
    cash_balance = income_lifetime - expenses_lifetime - salary_lifetime

    # ── Долги клиентов ────────────────────────────────────────────────────────
    debt_money = 0.0
    for s in sessions_paying:
        price = float(s.amount)
        paid = float(s.paid)
        debt_money += max(0.0, price - paid)
    for r in rituals_paying:
        price = float(r.price or 0)
        paid = float(r.paid)
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
