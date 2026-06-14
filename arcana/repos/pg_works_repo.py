"""arcana/repos/pg_works_repo.py — PostgreSQL adapter for 🔮 Работы."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import select

from arcana.repos.works_repo import Work
from arcana.repos.works_tables import works, work_priority, work_status
from core.db import get_engine

logger = logging.getLogger("arcana.pg_works")

_PRIORITY_TO_CODE = {
    "срочно":      "urgent",
    "важно":       "important",
    "можно потом": "later",
    "urgent":      "urgent",
    "important":   "important",
    "later":       "later",
}


def _code_for(mapping: dict, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return mapping.get(raw.lower().strip())


def _resolve(conn, table, code: Optional[str]) -> Optional[int]:
    if not code:
        return None
    row = conn.execute(
        select(table.c.id).where(table.c.code == code)
    ).fetchone()
    return row[0] if row else None


def _row_to_work(row) -> Work:
    deadline_str = ""
    if row.deadline:
        d = row.deadline
        deadline_str = f" · 📅 {d.day:02d}.{d.month:02d}"
        if d.hour or d.minute:
            deadline_str += f" {d.hour:02d}:{d.minute:02d}"
    cat = row.category or ""
    return Work(
        id=str(row.id),
        title=row.title or "",
        priority=row.priority_label or "Можно потом",
        deadline_str=deadline_str,
        category_str=f" · {cat}" if cat else "",
        has_client=bool(row.client_id),
    )


def _select_works():
    p = work_priority.alias("p")
    return (
        select(
            works.c.id,
            works.c.title,
            works.c.deadline,
            works.c.category,
            works.c.client_id,
            p.c.label.label("priority_label"),
            work_status.c.code.label("status_code"),
        )
        .outerjoin(p,           works.c.priority_id == p.c.id)
        .outerjoin(work_status, works.c.status_id   == work_status.c.id)
    )


class PgWorksRepo:

    def _list_open_sync(self, user_notion_id: str) -> List[Work]:
        stmt = (
            _select_works()
            .where(work_status.c.code != "done")
            .order_by(works.c.deadline.asc().nullslast())
        )
        if user_notion_id:
            stmt = stmt.where(works.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_work(r) for r in rows]

    def _create_sync(
        self,
        title: str,
        priority: str,
        deadline: Optional[datetime],
        category: Optional[str],
        client_id: Optional[str],
        user_notion_id: str,
    ) -> Optional[str]:
        pcode = _code_for(_PRIORITY_TO_CODE, priority) or "later"
        with get_engine().begin() as conn:
            open_id  = _resolve(conn, work_status,   "open")
            prio_id  = _resolve(conn, work_priority, pcode)
            cid_int  = int(client_id) if client_id and client_id.isdigit() else None
            row = conn.execute(
                works.insert().values(
                    title=title,
                    deadline=deadline,
                    category=category or None,
                    priority_id=prio_id,
                    status_id=open_id,
                    client_id=cid_int,
                    user_notion_id=user_notion_id or None,
                ).returning(works.c.id)
            ).fetchone()
        return str(row[0]) if row else None

    def _mark_done_sync(self, work_id: str) -> bool:
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            done_id = _resolve(conn, work_status, "done")
            res = conn.execute(
                works.update()
                .where(works.c.id == wid)
                .values(status_id=done_id)
            )
        return res.rowcount > 0

    # ── Public async interface ────────────────────────────────────────────────

    async def list_open(self, user_notion_id: str = "") -> List[Work]:
        return await asyncio.to_thread(self._list_open_sync, user_notion_id)

    async def create(
        self,
        title: str,
        priority: str = "Можно потом",
        deadline: Optional[datetime] = None,
        category: Optional[str] = None,
        client_id: Optional[str] = None,
        user_notion_id: str = "",
    ) -> Optional[str]:
        return await asyncio.to_thread(
            self._create_sync,
            title, priority, deadline, category, client_id, user_notion_id,
        )

    async def mark_done(self, work_id: str) -> bool:
        return await asyncio.to_thread(self._mark_done_sync, work_id)
