"""core/repos/pg_cushion_repo.py — financial cushion repository (#подушка).

Подушка — отдельная сущность, не цель_-факт памяти. Один ряд на пользователя.
balance инкрементируется (никогда не перезаписывается), target и
monthly_contribution — правятся.

Async методы = asyncio.to_thread над sync SQLAlchemy (как pg_debts_repo).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from core.repos.cushion_table import cushion

logger = logging.getLogger("core.pg_cushion_repo")


def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


def _now():
    return datetime.now(timezone.utc)


@dataclass
class Cushion:
    user_notion_id: str = ""
    balance: float = 0.0
    target: Optional[float] = None
    monthly_contribution: float = 0.0
    updated_at: str = ""


def _row_to_cushion(row) -> Cushion:
    return Cushion(
        user_notion_id=row.user_notion_id or "",
        balance=float(row.balance or 0),
        target=(float(row.target) if row.target is not None else None),
        monthly_contribution=float(row.monthly_contribution or 0),
        updated_at=(row.updated_at.isoformat() if isinstance(row.updated_at, datetime)
                    else str(row.updated_at or "")),
    )


class PgCushionRepo:

    def _get_row_sync(self, conn, user_notion_id: str):
        return conn.execute(
            select(cushion).where(cushion.c.user_notion_id == user_notion_id)
        ).fetchone()

    def _get_sync(self, user_notion_id: str) -> Optional[Cushion]:
        with _get_engine().connect() as conn:
            row = self._get_row_sync(conn, user_notion_id)
            return _row_to_cushion(row) if row else None

    def _ensure_row_sync(self, conn, user_notion_id: str):
        row = self._get_row_sync(conn, user_notion_id)
        if row is None:
            conn.execute(cushion.insert().values(
                user_notion_id=user_notion_id, balance=0, target=None,
                monthly_contribution=0, created_at=_now(), updated_at=_now(),
            ))
            row = self._get_row_sync(conn, user_notion_id)
        return row

    def _add_to_balance_sync(self, user_notion_id: str, amount: float) -> float:
        with _get_engine().begin() as conn:
            row = self._ensure_row_sync(conn, user_notion_id)
            new_balance = float(row.balance or 0) + float(amount or 0)
            conn.execute(
                cushion.update().where(cushion.c.id == row.id)
                .values(balance=new_balance, updated_at=_now())
            )
            return new_balance

    def _set_target_sync(self, user_notion_id: str, target: Optional[float]) -> None:
        with _get_engine().begin() as conn:
            row = self._ensure_row_sync(conn, user_notion_id)
            conn.execute(
                cushion.update().where(cushion.c.id == row.id)
                .values(target=target, updated_at=_now())
            )

    def _set_monthly_contribution_sync(self, user_notion_id: str, amount: float) -> None:
        with _get_engine().begin() as conn:
            row = self._ensure_row_sync(conn, user_notion_id)
            conn.execute(
                cushion.update().where(cushion.c.id == row.id)
                .values(monthly_contribution=float(amount or 0), updated_at=_now())
            )

    # ── async API ────────────────────────────────────────────────────────────

    async def get(self, user_notion_id: str) -> Optional[Cushion]:
        return await asyncio.to_thread(self._get_sync, user_notion_id)

    async def add_to_balance(self, user_notion_id: str, amount: float) -> float:
        """Прибавить к балансу (инкремент, не перезапись). Возвращает новый баланс."""
        return await asyncio.to_thread(self._add_to_balance_sync, user_notion_id, amount)

    async def set_target(self, user_notion_id: str, target: Optional[float]) -> None:
        await asyncio.to_thread(self._set_target_sync, user_notion_id, target)

    async def set_monthly_contribution(self, user_notion_id: str, amount: float) -> None:
        await asyncio.to_thread(self._set_monthly_contribution_sync, user_notion_id, amount)


_repo = PgCushionRepo()
