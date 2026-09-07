"""core/repos/pg_goals_repo.py — savings-goals repository (#205).

Раньше цель = факт Памяти `цель_*`. Теперь своя таблица `goals`
(status: active | achieved | dropped). Бюджет читает `monthly` активных
целей (аналог старого `saving`); `saved` — ручной трекинг накоплений.

Async методы через asyncio.to_thread над sync SQLAlchemy (как pg_debts_repo).
Имя матчится регистронезависимо (exact lower).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select

from core.repos.goals_table import goals

logger = logging.getLogger("core.pg_goals_repo")


def _get_engine():
    from arcana.repos.pg_sessions_repo import get_engine
    return get_engine()


def _now():
    return datetime.now(timezone.utc)


@dataclass
class Goal:
    id: str = ""
    user_id: str = ""
    name: str = ""
    target: float = 0.0
    monthly: float = 0.0
    saved: float = 0.0
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""


def _ts(v) -> str:
    if v is None:
        return ""
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _row_to_goal(row) -> Goal:
    return Goal(
        id=str(row.id),
        user_id=row.user_id or "",
        name=row.name or "",
        target=float(row.target or 0),
        monthly=float(row.monthly or 0),
        saved=float(row.saved or 0),
        status=row.status or "active",
        created_at=_ts(row.created_at),
        updated_at=_ts(row.updated_at),
        closed_at=_ts(row.closed_at),
    )


class PgGoalsRepo:

    def _find_row_sync(self, conn, user_id: str, name: str, active_only: bool = False):
        # Сравнение имени в Python: SQLite lower() не трогает кириллицу, а
        # PG-lower() трогает — единообразие важнее одного лишнего SELECT.
        needle = (name or "").strip().lower()
        q = select(goals).where(goals.c.user_id == (user_id or ""))
        if active_only:
            q = q.where(goals.c.status == "active")
        for row in conn.execute(q).fetchall():
            if (row.name or "").strip().lower() == needle:
                return row
        return None

    def _upsert_sync(self, user_id: str, name: str, target: float,
                     monthly: float) -> None:
        name = (name or "").strip()
        with _get_engine().begin() as conn:
            row = self._find_row_sync(conn, user_id, name)
            if row is None:
                conn.execute(goals.insert().values(
                    user_id=user_id or "", name=name,
                    target=target, monthly=monthly, saved=0,
                    status="active", created_at=_now(), updated_at=_now(),
                ))
            else:
                conn.execute(goals.update().where(goals.c.id == row.id).values(
                    name=name, target=target, monthly=monthly,
                    status="active", closed_at=None, updated_at=_now(),
                ))

    def _set_status_sync(self, user_id: str, name: str, status: str) -> bool:
        with _get_engine().begin() as conn:
            row = self._find_row_sync(conn, user_id, name, active_only=True)
            if row is None:
                return False
            conn.execute(goals.update().where(goals.c.id == row.id).values(
                status=status,
                closed_at=_now() if status != "active" else None,
                updated_at=_now(),
            ))
            return True

    def _add_saved_sync(self, user_id: str, name: str, amount: float) -> Optional[float]:
        with _get_engine().begin() as conn:
            row = self._find_row_sync(conn, user_id, name, active_only=True)
            if row is None:
                return None
            new_saved = float(row.saved or 0) + float(amount)
            vals = {"saved": new_saved, "updated_at": _now()}
            # накопили цель → achieved
            if float(row.target or 0) > 0 and new_saved >= float(row.target):
                vals["status"] = "achieved"
                vals["closed_at"] = _now()
            conn.execute(goals.update().where(goals.c.id == row.id).values(**vals))
            return new_saved

    def _list_sync(self, user_id: str, statuses: tuple) -> List[Goal]:
        q = select(goals).where(goals.c.user_id == (user_id or ""))
        if statuses:
            q = q.where(goals.c.status.in_(statuses))
        q = q.order_by(goals.c.created_at.asc())
        with _get_engine().connect() as conn:
            return [_row_to_goal(r) for r in conn.execute(q).fetchall()]

    # ── async API ────────────────────────────────────────────────────────────

    async def upsert(self, user_id: str, name: str, target: float = 0.0,
                     monthly: float = 0.0) -> None:
        await asyncio.to_thread(self._upsert_sync, user_id, name, target, monthly)

    async def set_status(self, user_id: str, name: str, status: str) -> bool:
        return await asyncio.to_thread(self._set_status_sync, user_id, name, status)

    async def add_saved(self, user_id: str, name: str, amount: float) -> Optional[float]:
        return await asyncio.to_thread(self._add_saved_sync, user_id, name, amount)

    async def list_active(self, user_id: str) -> List[Goal]:
        return await asyncio.to_thread(self._list_sync, user_id, ("active",))

    async def list_closed(self, user_id: str) -> List[Goal]:
        return await asyncio.to_thread(
            self._list_sync, user_id, ("achieved", "dropped")
        )

    async def list_all(self, user_id: str) -> List[Goal]:
        return await asyncio.to_thread(self._list_sync, user_id, ())


_repo = PgGoalsRepo()
