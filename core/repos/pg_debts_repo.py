"""core/repos/pg_debts_repo.py — personal debt ledger repository (#8).

kind='i_owe'   — Кай должна кому-то (бюджет, finance.py, load_budget_data)
kind='they_owe' — кто-то должен Кай (будущий экран «мне должны»)

All async methods use asyncio.to_thread over sync SQLAlchemy (no asyncpg).
Name-matching is always case-insensitive: lower(name) == lower(incoming).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select

from core.repos.debts_table import debts

logger = logging.getLogger("core.pg_debts_repo")


def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


def _now():
    return datetime.now(timezone.utc)


# ── Domain ────────────────────────────────────────────────────────────────────

@dataclass
class Debt:
    id: str = ""
    user_notion_id: str = ""
    name: str = ""
    kind: str = "i_owe"
    amount: float = 0.0
    deadline: str = ""
    strategy: str = ""
    monthly_payment: float = 0.0
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


def _row_to_debt(row) -> Debt:
    def _ts(val) -> str:
        if val is None:
            return ""
        if isinstance(val, datetime):
            return val.isoformat()
        return str(val)

    return Debt(
        id=str(row.id),
        user_notion_id=row.user_notion_id or "",
        name=row.name or "",
        kind=row.kind or "i_owe",
        amount=float(row.amount or 0),
        deadline=row.deadline or "",
        strategy=row.strategy or "",
        monthly_payment=float(row.monthly_payment or 0),
        is_active=bool(row.is_active),
        created_at=_ts(row.created_at),
        updated_at=_ts(row.updated_at),
    )


# ── Repo ──────────────────────────────────────────────────────────────────────

class PgDebtsRepo:

    def _find_row_sync(self, conn, user_notion_id: str, kind: str, name: str,
                       active_only: bool = False):
        """Case-insensitive row lookup in Python (SQLite lower() is ASCII-only)."""
        q = select(debts).where(
            (debts.c.user_notion_id == user_notion_id)
            & (debts.c.kind == kind)
        )
        if active_only:
            q = q.where(debts.c.is_active == True)
        rows = conn.execute(q).fetchall()
        name_low = name.lower()
        return next((r for r in rows if (r.name or "").lower() == name_low), None)

    def _upsert_sync(
        self,
        user_notion_id: str,
        name: str,
        kind: str,
        amount: float,
        deadline: Optional[str],
        strategy: Optional[str],
        monthly_payment: float,
    ) -> None:
        """SELECT → INSERT or UPDATE (case-insensitive name match).

        Manual upsert so SQLite in tests works and no PG-only ON CONFLICT
        expression syntax leaks into application code.
        The DB expression index (uq_debts_owner_kind_name) is the atomic
        safety net for concurrent PG writes.
        """
        with _get_engine().begin() as conn:
            row = self._find_row_sync(conn, user_notion_id, kind, name)

            if row is None:
                conn.execute(debts.insert().values(
                    user_notion_id=user_notion_id,
                    name=name,
                    kind=kind,
                    amount=amount,
                    deadline=deadline,
                    strategy=strategy,
                    monthly_payment=monthly_payment,
                    is_active=True,
                    created_at=_now(),
                    updated_at=_now(),
                ))
            else:
                conn.execute(
                    debts.update()
                    .where(debts.c.id == row.id)
                    .values(
                        amount=amount,
                        deadline=deadline,
                        strategy=strategy,
                        monthly_payment=monthly_payment,
                        is_active=True,
                        updated_at=_now(),
                    )
                )

    def _reduce_amount_sync(
        self,
        user_notion_id: str,
        kind: str,
        name: str,
        payment: float,
    ) -> Optional[Tuple[float, bool]]:
        """Subtract payment from amount. Deactivates if result <= 0.

        Returns (new_amount, closed) or None if debt not found.
        """
        with _get_engine().begin() as conn:
            row = self._find_row_sync(conn, user_notion_id, kind, name, active_only=True)

            if row is None:
                return None

            new_amount = max(0.0, float(row.amount) - payment)
            closed = new_amount <= 0
            conn.execute(
                debts.update()
                .where(debts.c.id == row.id)
                .values(
                    amount=new_amount,
                    is_active=not closed,
                    updated_at=_now(),
                )
            )
            return new_amount, closed

    def _deactivate_sync(
        self,
        user_notion_id: str,
        kind: str,
        name: str,
    ) -> bool:
        """Set is_active=False. Returns True if row was found and updated."""
        with _get_engine().begin() as conn:
            row = self._find_row_sync(conn, user_notion_id, kind, name, active_only=True)

            if row is None:
                return False

            conn.execute(
                debts.update()
                .where(debts.c.id == row.id)
                .values(is_active=False, updated_at=_now())
            )
            return True

    def _list_active_sync(
        self,
        user_notion_id: str,
        kind: Optional[str],
    ) -> List[Debt]:
        with _get_engine().connect() as conn:
            q = select(debts).where(
                (debts.c.user_notion_id == user_notion_id)
                & (debts.c.is_active == True)
            )
            if kind is not None:
                q = q.where(debts.c.kind == kind)
            q = q.order_by(debts.c.created_at)
            return [_row_to_debt(r) for r in conn.execute(q).fetchall()]

    # ── async API ──────────────────────────────────────────────────────────────

    async def upsert(
        self,
        user_notion_id: str,
        name: str,
        kind: str = "i_owe",
        amount: float = 0.0,
        deadline: Optional[str] = None,
        strategy: Optional[str] = None,
        monthly_payment: float = 0.0,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_sync,
            user_notion_id, name, kind, amount, deadline, strategy, monthly_payment,
        )

    async def reduce_amount(
        self,
        user_notion_id: str,
        kind: str,
        name: str,
        payment: float,
    ) -> Optional[Tuple[float, bool]]:
        return await asyncio.to_thread(
            self._reduce_amount_sync, user_notion_id, kind, name, payment,
        )

    async def deactivate(
        self,
        user_notion_id: str,
        kind: str,
        name: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._deactivate_sync, user_notion_id, kind, name,
        )

    async def list_active(
        self,
        user_notion_id: str,
        kind: Optional[str] = None,
    ) -> List[Debt]:
        return await asyncio.to_thread(
            self._list_active_sync, user_notion_id, kind,
        )


_repo = PgDebtsRepo()
