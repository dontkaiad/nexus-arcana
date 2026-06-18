"""core/repos/pg_finance_repo.py — PG implementation for 💰 Финансы (split by Бот).

nexus_budget → PgNexusBudgetRepo  + BudgetEntry domain object
arcana_pnl   → PgArcanaPnlRepo    + PnlEntry domain object

All async methods use asyncio.to_thread over sync SQLAlchemy (no asyncpg).
GUARD: source='🔄 Бартер' is rejected at FinanceRepo level for nexus_budget.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import and_, desc, select, text

from core.repos.finance_table import arcana_pnl, nexus_budget

logger = logging.getLogger("core.pg_finance_repo")

BARTER_SOURCE = "🔄 Бартер"


def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


def _parse_date(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.isoformat()[:10]
    return str(val)[:10]


def _month_range(month: str):
    """'YYYY-MM' → (date_first, date_last)."""
    year, mon = int(month[:4]), int(month[5:7])
    last_day = calendar.monthrange(year, mon)[1]
    return date(year, mon, 1), date(year, mon, last_day)


# ── Domain objects ─────────────────────────────────────────────────────────────

@dataclass
class BudgetEntry:
    """One row from nexus_budget."""
    id: str = ""
    description: str = ""
    amount: float = 0.0
    category: str = ""
    type_: str = ""
    source: str = ""
    date: str = ""
    user_notion_id: str = ""


@dataclass
class PnlEntry:
    """One row from arcana_pnl."""
    id: str = ""
    description: str = ""
    amount: float = 0.0
    category: str = ""
    type_: str = ""
    source: str = ""
    date: str = ""
    user_notion_id: str = ""


def _row_to_budget(row) -> BudgetEntry:
    return BudgetEntry(
        id=str(row.id),
        description=row.description or "",
        amount=float(row.amount or 0),
        category=row.category or "",
        type_=row.type_ or "",
        source=row.source or "",
        date=_parse_date(row.date),
        user_notion_id=row.user_notion_id or "",
    )


def _row_to_pnl(row) -> PnlEntry:
    return PnlEntry(
        id=str(row.id),
        description=row.description or "",
        amount=float(row.amount or 0),
        category=row.category or "",
        type_=row.type_ or "",
        source=row.source or "",
        date=_parse_date(row.date),
        user_notion_id=row.user_notion_id or "",
    )


def _type_cond(table, type_filter: str):
    """Build SQLAlchemy type filter. 'expense'→%Расход%, 'income'→%Доход%, else exact."""
    col = table.c.type_
    if not type_filter:
        return None
    if type_filter == "expense":
        return col.like("%Расход%")
    if type_filter == "income":
        return col.like("%Доход%")
    return col == type_filter


# ── PgNexusBudgetRepo ──────────────────────────────────────────────────────────

def _nb_add_sync(description: str, amount: float, category: str, type_: str,
                  source: str, date_val, user_notion_id: str) -> str:
    ins = nexus_budget.insert().values(
        description=description,
        amount=amount,
        category=category,
        type_=type_,
        source=source,
        date=date_val,
        user_notion_id=user_notion_id,
    )
    with _get_engine().begin() as conn:
        result = conn.execute(ins)
        return str(result.inserted_primary_key[0])


def _nb_query_sync(date_from: str, date_to: str, type_: Optional[str],
                    category: Optional[str], page_size: int,
                    user_notion_id: str = "") -> List[BudgetEntry]:
    # fail-closed: никогда не агрегируем данные разных пользователей (#139)
    if not user_notion_id:
        return []
    conds = [
        nexus_budget.c.date >= date_from,
        nexus_budget.c.date <= date_to,
    ]
    if type_:
        conds.append(nexus_budget.c.type_ == type_)
    if category:
        conds.append(nexus_budget.c.category == category)
    if user_notion_id:
        conds.append(nexus_budget.c.user_notion_id == user_notion_id)
    q = (
        select(nexus_budget)
        .where(and_(*conds))
        .order_by(desc(nexus_budget.c.date))
        .limit(page_size)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_budget(r) for r in rows]


def _nb_search_desc_sync(text: str, page_size: int,
                          user_notion_id: str = "") -> List[BudgetEntry]:
    # fail-closed: никогда не возвращаем данные без привязки к юзеру (#139)
    if not user_notion_id:
        return []
    conds = [nexus_budget.c.description.ilike(f"%{text}%")]
    if user_notion_id:
        conds.append(nexus_budget.c.user_notion_id == user_notion_id)
    q = (
        select(nexus_budget)
        .where(and_(*conds))
        .order_by(desc(nexus_budget.c.date))
        .limit(page_size)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_budget(r) for r in rows]


def _nb_query_month_sync(month: str, description_filter: str,
                          type_filter: str,
                          user_notion_id: str = "") -> List[BudgetEntry]:
    # fail-closed: никогда не агрегируем данные разных пользователей (#139)
    if not user_notion_id:
        return []
    d_from, d_to = _month_range(month)
    conds = [
        nexus_budget.c.date >= d_from,
        nexus_budget.c.date <= d_to,
    ]
    tc = _type_cond(nexus_budget, type_filter)
    if tc is not None:
        conds.append(tc)
    if description_filter:
        conds.append(nexus_budget.c.description.ilike(f"%{description_filter}%"))
    if user_notion_id:
        conds.append(nexus_budget.c.user_notion_id == user_notion_id)
    q = (
        select(nexus_budget)
        .where(and_(*conds))
        .order_by(desc(nexus_budget.c.date))
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_budget(r) for r in rows]


def _nb_update_sync(row_id: str, **fields) -> bool:
    allowed = {"description", "amount", "category", "type_", "source", "date"}
    upd = {k: v for k, v in fields.items() if k in allowed}
    if not upd:
        return False
    with _get_engine().begin() as conn:
        result = conn.execute(
            nexus_budget.update()
            .where(nexus_budget.c.id == int(row_id))
            .values(**upd)
        )
    return bool(result.rowcount)


def _nb_latest_by_type_sync(target_type: str) -> Optional[str]:
    """Return id of most recent row with type_ matching target_type."""
    type_str = "💸 Расход" if target_type == "expense" else "💰 Доход"
    q = (
        select(nexus_budget.c.id)
        .where(nexus_budget.c.type_ == type_str)
        .order_by(desc(nexus_budget.c.date), desc(nexus_budget.c.id))
        .limit(1)
    )
    with _get_engine().connect() as conn:
        row = conn.execute(q).fetchone()
    return str(row[0]) if row else None


class PgNexusBudgetRepo:
    async def add_entry(self, description: str, amount: float, category: str,
                         type_: str, source: str, date_iso: str,
                         user_notion_id: str) -> str:
        try:
            d = date.fromisoformat(date_iso[:10])
        except ValueError:
            d = None
        return await asyncio.to_thread(
            _nb_add_sync, description, amount, category, type_, source, d, user_notion_id
        )

    async def query(self, date_from: str, date_to: str, type_: Optional[str] = None,
                     category: Optional[str] = None, page_size: int = 200,
                     user_notion_id: str = "") -> List[BudgetEntry]:
        return await asyncio.to_thread(
            _nb_query_sync, date_from, date_to, type_, category, page_size, user_notion_id
        )

    async def query_month(self, month: str, description_filter: str = "",
                           type_filter: str = "",
                           user_notion_id: str = "") -> List[BudgetEntry]:
        return await asyncio.to_thread(
            _nb_query_month_sync, month, description_filter, type_filter, user_notion_id
        )

    async def search_description(self, text: str, page_size: int = 5,
                                  user_notion_id: str = "") -> List[BudgetEntry]:
        if not text:
            return []
        return await asyncio.to_thread(_nb_search_desc_sync, text, page_size, user_notion_id)

    async def update(self, row_id: str, **fields) -> bool:
        return await asyncio.to_thread(_nb_update_sync, row_id, **fields)

    async def latest_id_by_type(self, target_type: str) -> Optional[str]:
        return await asyncio.to_thread(_nb_latest_by_type_sync, target_type)


# ── PgArcanaPnlRepo ───────────────────────────────────────────────────────────

def _ap_add_sync(description: str, amount: float, category: str, type_: str,
                  source: str, date_val, user_notion_id: str) -> str:
    ins = arcana_pnl.insert().values(
        description=description,
        amount=amount,
        category=category,
        type_=type_,
        source=source,
        date=date_val,
        user_notion_id=user_notion_id,
    )
    with _get_engine().begin() as conn:
        result = conn.execute(ins)
        return str(result.inserted_primary_key[0])


def _ap_query_sync(date_from: str, date_to: str, type_: Optional[str],
                    category: Optional[str], page_size: int,
                    user_notion_id: str = "") -> List[PnlEntry]:
    # fail-closed: никогда не агрегируем данные разных пользователей (#139)
    if not user_notion_id:
        return []
    conds = [
        arcana_pnl.c.date >= date_from,
        arcana_pnl.c.date <= date_to,
    ]
    if type_:
        conds.append(arcana_pnl.c.type_ == type_)
    if category:
        conds.append(arcana_pnl.c.category == category)
    if user_notion_id:
        conds.append(arcana_pnl.c.user_notion_id == user_notion_id)
    q = (
        select(arcana_pnl)
        .where(and_(*conds))
        .order_by(desc(arcana_pnl.c.date))
        .limit(page_size)
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_pnl(r) for r in rows]


def _ap_query_month_sync(month: str, description_filter: str,
                          type_filter: str,
                          user_notion_id: str = "") -> List[PnlEntry]:
    # fail-closed: никогда не агрегируем данные разных пользователей (#139)
    if not user_notion_id:
        return []
    d_from, d_to = _month_range(month)
    conds = [
        arcana_pnl.c.date >= d_from,
        arcana_pnl.c.date <= d_to,
    ]
    tc = _type_cond(arcana_pnl, type_filter)
    if tc is not None:
        conds.append(tc)
    if description_filter:
        conds.append(arcana_pnl.c.description.ilike(f"%{description_filter}%"))
    if user_notion_id:
        conds.append(arcana_pnl.c.user_notion_id == user_notion_id)
    q = (
        select(arcana_pnl)
        .where(and_(*conds))
        .order_by(desc(arcana_pnl.c.date))
    )
    with _get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_pnl(r) for r in rows]


def _ap_update_sync(row_id: str, **fields) -> bool:
    allowed = {"description", "amount", "category", "type_", "source", "date"}
    upd = {k: v for k, v in fields.items() if k in allowed}
    if not upd:
        return False
    with _get_engine().begin() as conn:
        result = conn.execute(
            arcana_pnl.update()
            .where(arcana_pnl.c.id == int(row_id))
            .values(**upd)
        )
    return bool(result.rowcount)


def _ap_latest_by_type_sync(target_type: str) -> Optional[str]:
    type_str = "💸 Расход" if target_type == "expense" else "💰 Доход"
    q = (
        select(arcana_pnl.c.id)
        .where(arcana_pnl.c.type_ == type_str)
        .order_by(desc(arcana_pnl.c.date), desc(arcana_pnl.c.id))
        .limit(1)
    )
    with _get_engine().connect() as conn:
        row = conn.execute(q).fetchone()
    return str(row[0]) if row else None


class PgArcanaPnlRepo:
    async def add_entry(self, description: str, amount: float, category: str,
                         type_: str, source: str, date_iso: str,
                         user_notion_id: str) -> str:
        try:
            d = date.fromisoformat(date_iso[:10])
        except ValueError:
            d = None
        return await asyncio.to_thread(
            _ap_add_sync, description, amount, category, type_, source, d, user_notion_id
        )

    async def query(self, date_from: str, date_to: str, type_: Optional[str] = None,
                     category: Optional[str] = None, page_size: int = 200,
                     user_notion_id: str = "") -> List[PnlEntry]:
        return await asyncio.to_thread(
            _ap_query_sync, date_from, date_to, type_, category, page_size, user_notion_id
        )

    async def query_month(self, month: str, description_filter: str = "",
                           type_filter: str = "",
                           user_notion_id: str = "") -> List[PnlEntry]:
        return await asyncio.to_thread(
            _ap_query_month_sync, month, description_filter, type_filter, user_notion_id
        )

    async def update(self, row_id: str, **fields) -> bool:
        return await asyncio.to_thread(_ap_update_sync, row_id, **fields)

    async def latest_id_by_type(self, target_type: str) -> Optional[str]:
        return await asyncio.to_thread(_ap_latest_by_type_sync, target_type)
