"""core/repos/finance_repo.py — repository seam for 💰 Финансы (shared Nexus + Arcana).

Финансы — общий домен, split по полю «Бот»:
  nexus_budget  → PgNexusBudgetRepo  (personal finance)
  arcana_pnl    → PgArcanaPnlRepo    (practice P&L)

GUARD: source='🔄 Бартер' → ONLY arcana_pnl; for nexus sanitised to '💳 Карта'.

Read methods (query_records, month) return List[FinanceEntry] — no raw Notion props.
Write methods (add, create_entry) route by bot_label to the correct PG table.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from core.repos.pg_finance_repo import (
    BudgetEntry,
    BARTER_SOURCE,
    PgNexusBudgetRepo,
    PgArcanaPnlRepo,
    PnlEntry,
)

logger = logging.getLogger("core.finance_repo")

_nexus_repo = PgNexusBudgetRepo()
_arcana_repo = PgArcanaPnlRepo()


@dataclass
class FinanceEntry:
    """Domain representation of one finance record (nexus_budget or arcana_pnl)."""
    id: str = ""
    description: str = ""
    amount: float = 0.0
    category: str = ""
    type_: str = ""
    source: str = ""
    date: str = ""
    bot: str = ""   # "☀️ Nexus" | "🌒 Arcana"


def _budget_to_fe(e: BudgetEntry, bot: str = "☀️ Nexus") -> FinanceEntry:
    return FinanceEntry(
        id=e.id, description=e.description, amount=e.amount,
        category=e.category, type_=e.type_, source=e.source,
        date=e.date, bot=bot,
    )


def _pnl_to_fe(e: PnlEntry, bot: str = "🌒 Arcana") -> FinanceEntry:
    return FinanceEntry(
        id=e.id, description=e.description, amount=e.amount,
        category=e.category, type_=e.type_, source=e.source,
        date=e.date, bot=bot,
    )


def _is_arcana(bot_label: str) -> bool:
    return "Arcana" in bot_label or "🌒" in bot_label


def _guard_source(source: str, bot_label: str) -> str:
    """Barter guard: nexus_budget must never have source='🔄 Бартер'."""
    if source == BARTER_SOURCE and not _is_arcana(bot_label):
        logger.warning("FINANCE BARTER GUARD: sanitising source → '💳 Карта' for %s", bot_label)
        return "💳 Карта"
    return source


class FinanceRepo:
    # ── canonical writes ──────────────────────────────────────────────────────

    async def add(
        self,
        *,
        date: str,
        amount: float,
        category: str,
        type_: str,
        source: str = "💳 Карта",
        bot_label: str = "☀️ Nexus",
        description: str = "",
        user_notion_id: str = "",
    ) -> Optional[str]:
        """Add a finance record. Routes to nexus_budget or arcana_pnl by bot_label."""
        source = _guard_source(source, bot_label)
        if _is_arcana(bot_label):
            return await _arcana_repo.add_entry(
                description=description, amount=float(amount),
                category=category, type_=type_, source=source,
                date_iso=date, user_notion_id=user_notion_id,
            )
        return await _nexus_repo.add_entry(
            description=description, amount=float(amount),
            category=category, type_=type_, source=source,
            date_iso=date, user_notion_id=user_notion_id,
        )

    async def create_entry(
        self,
        db_id: str,      # kept for backward compat; ignored (PG doesn't need it)
        *,
        description: str,
        date: str,
        amount: float,
        category: str,
        type_: str,
        source: str,
        bot_label: str,
        user_notion_id: str = "",
    ) -> Optional[str]:
        """Create finance record (PG). db_id is ignored."""
        return await self.add(
            date=date, amount=amount, category=category, type_=type_,
            source=source, bot_label=bot_label, description=description,
            user_notion_id=user_notion_id,
        )

    async def update_last(self, target_type: str, field: str, new_value: str) -> bool:
        """Update the most-recent record of a given type (expense or income).

        Searches nexus_budget first (latest), then arcana_pnl if no row found.
        """
        row_id = await _nexus_repo.latest_id_by_type(target_type)
        if row_id:
            return await self.update_field(row_id, field, new_value)
        row_id = await _arcana_repo.latest_id_by_type(target_type)
        if row_id:
            return await self.update_field(row_id, field, new_value)
        return False

    # ── field updates ─────────────────────────────────────────────────────────

    async def update_field(self, page_id: str, field: str, value: str) -> bool:
        """Update one field on a finance row (by PG id). Returns True on success.

        Tries nexus_budget first, then arcana_pnl.
        Field names match FinanceEntry attributes: source, category, description, amount, type_.
        """
        field_map = {
            "source": "source",
            "category": "category",
            "description": "description",
            "type_": "type_",
        }
        if field == "amount":
            try:
                kw = {"amount": float(value)}
            except ValueError:
                return False
        elif field in field_map:
            kw = {field_map[field]: value}
        else:
            return False

        try:
            ok = await _nexus_repo.update(page_id, **kw)
            if not ok:
                ok = await _arcana_repo.update(page_id, **kw)
            return ok
        except Exception as e:
            logger.error("update_field error: %s", e)
            return False

    # ── reads → return List[FinanceEntry] (no Notion raw props) ───────────────

    async def query_records(
        self,
        *,
        date_from: str,
        date_to: str,
        type_: Optional[str] = None,
        category: Optional[str] = None,
        page_size: int = 200,
        db_id: Optional[str] = None,  # backward compat, ignored
    ) -> List[FinanceEntry]:
        """Query records by date range. Unions nexus_budget + arcana_pnl."""
        nexus_rows, arcana_rows = await _query_both(
            date_from=date_from, date_to=date_to,
            type_=type_, category=category, page_size=page_size,
        )
        result = (
            [_budget_to_fe(r) for r in nexus_rows]
            + [_pnl_to_fe(r) for r in arcana_rows]
        )
        result.sort(key=lambda e: e.date, reverse=True)
        return result[:page_size]

    async def month(
        self,
        month: str,
        user_notion_id: str = "",
        description_filter: str = "",
        type_filter: str = "",
    ) -> List[FinanceEntry]:
        """Return all records for a month (YYYY-MM) from both tables."""
        nexus_rows = await _nexus_repo.query_month(month, description_filter, type_filter)
        arcana_rows = await _arcana_repo.query_month(month, description_filter, type_filter)
        result = (
            [_budget_to_fe(r) for r in nexus_rows]
            + [_pnl_to_fe(r) for r in arcana_rows]
        )
        result.sort(key=lambda e: e.date, reverse=True)
        return result


async def _query_both(
    date_from: str, date_to: str,
    type_: Optional[str], category: Optional[str], page_size: int,
):
    import asyncio as _asyncio
    return await _asyncio.gather(
        _nexus_repo.query(date_from, date_to, type_, category, page_size),
        _arcana_repo.query(date_from, date_to, type_, category, page_size),
    )


_repo = FinanceRepo()
