"""arcana/repos/pg_clients_repo.py — PostgreSQL adapter for 👥 Клиенты.

All writes go directly to PG. No Notion bridge.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from sqlalchemy import select

from arcana.repos.clients_repo import Client
from arcana.repos.clients_tables import client_type, client_status, clients
from core.db import get_engine

logger = logging.getLogger("arcana.pg_clients")

# Display label maps (exported for routes)
TYPE_CODE_TO_FULL = {
    "paid": "🤝 Платный",
    "free": "🎁 Бесплатный",
    "self": "🌟 Self",
}
STATUS_CODE_TO_LABEL = {
    "active":   "🟢 Активный",
    "one_time": "🌙 Разовый",
    "closed":   "⛔ Закрытый",
}

_NOTION_TYPE_TO_CODE = {
    "🎁 бесплатный": "free",
    "🤝 платный":    "paid",
    "🌟 self":        "self",
    "бесплатный": "free",
    "платный":    "paid",
    "self":        "self",
    "free": "free",
    "paid": "paid",
}

_NOTION_STATUS_TO_CODE = {
    "⛔ закрытый": "closed",
    "🌙 разовый":  "one_time",
    "🟢 активный": "active",
    "закрытый": "closed",
    "разовый":  "one_time",
    "активный": "active",
    "closed":   "closed",
    "one_time": "one_time",
    "active":   "active",
}


def _type_code(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return _NOTION_TYPE_TO_CODE.get(raw.lower().strip())


def _status_code(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return _NOTION_STATUS_TO_CODE.get(raw.lower().strip())


def _resolve_lookup(conn, table, code: Optional[str]) -> Optional[int]:
    if not code:
        return None
    row = conn.execute(select(table.c.id).where(table.c.code == code)).fetchone()
    return row[0] if row else None


def _row_to_client(row) -> Client:
    return Client(
        id=str(row.id),
        name=row.name or "",
        contact=row.contact or "",
        request=row.request or "",
        notes=row.notes or "",
        since="",
    )


def _select_clients_full():
    """Select all client fields joined with type/status lookup tables."""
    ct = client_type.alias("ct")
    cs = client_status.alias("cs")
    return (
        select(
            clients,
            ct.c.code.label("type_code"),
            cs.c.code.label("status_code"),
        )
        .outerjoin(ct, clients.c.type_id == ct.c.id)
        .outerjoin(cs, clients.c.status_id == cs.c.id)
        .order_by(clients.c.name)
    )


def _row_to_client_full(row) -> Client:
    bd = row.birthday
    return Client(
        id=str(row.id),
        name=row.name or "",
        contact=row.contact or "",
        request=row.request or "",
        notes=row.notes or "",
        since="",
        type_code=row.type_code or None,
        status_code=row.status_code or None,
        birthday=bd.isoformat() if bd else None,
        photo_url=row.photo_url or None,
        object_photos=row.object_photos or None,
    )


class PgClientsRepo:

    # ── Sync implementations ──────────────────────────────────────────────────

    def _find_sync(self, name: str) -> Optional[Client]:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(clients).where(clients.c.name.ilike(f"%{name}%"))
                .order_by(clients.c.id)
                .limit(1)
            ).fetchone()
        return _row_to_client(row) if row else None

    def _find_by_id_sync(self, pg_id: int) -> Optional[Client]:
        stmt = _select_clients_full().where(clients.c.id == pg_id)
        with get_engine().connect() as conn:
            row = conn.execute(stmt).fetchone()
        return _row_to_client_full(row) if row else None

    def _list_all_sync(self, user_notion_id: str) -> List[Client]:
        stmt = _select_clients_full()
        if user_notion_id:
            stmt = stmt.where(clients.c.user_notion_id == user_notion_id)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_client_full(r) for r in rows]

    def _find_self_sync(self, user_notion_id: str) -> Optional[Client]:
        """Find the self-type client (used in resolve_self_client)."""
        with get_engine().connect() as conn:
            self_id = _resolve_lookup(conn, client_type, "self")
            if self_id is None:
                return None
            stmt = select(clients).where(clients.c.type_id == self_id)
            if user_notion_id:
                stmt = stmt.where(clients.c.user_notion_id == user_notion_id)
            row = conn.execute(stmt.limit(1)).fetchone()
        return _row_to_client(row) if row else None

    def _create_sync(
        self,
        name: str,
        type_code: Optional[str],
        status_code: str = "active",
        contact: Optional[str] = None,
        request: Optional[str] = None,
        notes: Optional[str] = None,
        user_notion_id: Optional[str] = None,
    ) -> Optional[int]:
        with get_engine().begin() as conn:
            # Check existing by exact name first (avoid duplicates)
            existing = conn.execute(
                select(clients.c.id).where(clients.c.name.ilike(name))
            ).fetchone()
            if existing:
                return existing[0]

            type_id   = _resolve_lookup(conn, client_type,   type_code or "paid")
            status_id = _resolve_lookup(conn, client_status, status_code)
            row = conn.execute(
                clients.insert().values(
                    name=name,
                    type_id=type_id,
                    status_id=status_id,
                    contact=contact or None,
                    request=request or None,
                    notes=notes or None,
                    user_notion_id=user_notion_id or None,
                ).returning(clients.c.id)
            ).fetchone()
        return row[0] if row else None

    def _update_profile_sync(
        self,
        pg_id: int,
        *,
        contact: Optional[str],
        request: Optional[str],
        notes: Optional[str],
        birthday: Optional[str],
        photo_url: Optional[str] = None,
        object_photos: Optional[str] = None,
        type_code: Optional[str] = None,
    ) -> None:
        vals = {}
        if contact is not None:
            vals["contact"] = contact
        if request is not None:
            vals["request"] = request
        if notes is not None:
            vals["notes"] = notes
        if birthday is not None:
            from datetime import date as _date
            try:
                vals["birthday"] = _date.fromisoformat(birthday)
            except ValueError:
                pass
        if photo_url is not None:
            vals["photo_url"] = photo_url
        if object_photos is not None:
            vals["object_photos"] = object_photos
        if not vals and type_code is None:
            return
        with get_engine().begin() as conn:
            if type_code is not None:
                type_id = _resolve_lookup(conn, client_type, type_code)
                if type_id is not None:
                    vals["type_id"] = type_id
            if vals:
                conn.execute(clients.update().where(clients.c.id == pg_id).values(**vals))

    def _get_object_photos_sync(self, pg_id: int) -> str:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(clients.c.object_photos).where(clients.c.id == pg_id)
            ).fetchone()
        return (row[0] or "") if row else ""

    # ── Public async interface ────────────────────────────────────────────────

    async def find(self, name: str) -> Optional[Client]:
        return await asyncio.to_thread(self._find_sync, name)

    async def find_by_id(self, pg_id: int) -> Optional[Client]:
        return await asyncio.to_thread(self._find_by_id_sync, pg_id)

    async def list_all(self, user_notion_id: str = "") -> List[Client]:
        return await asyncio.to_thread(self._list_all_sync, user_notion_id)

    async def find_self(self, user_notion_id: str = "") -> Optional[Client]:
        return await asyncio.to_thread(self._find_self_sync, user_notion_id)

    async def create(
        self,
        name: str,
        type_code: Optional[str] = "paid",
        status_code: str = "active",
        contact: Optional[str] = None,
        request: Optional[str] = None,
        notes: Optional[str] = None,
        user_notion_id: Optional[str] = None,
    ) -> Optional[int]:
        return await asyncio.to_thread(
            self._create_sync,
            name, type_code, status_code, contact, request, notes, user_notion_id,
        )

    async def update_profile(
        self,
        pg_id: int,
        *,
        contact: Optional[str] = None,
        request: Optional[str] = None,
        notes: Optional[str] = None,
        birthday: Optional[str] = None,
        photo_url: Optional[str] = None,
        object_photos: Optional[str] = None,
        type_code: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_profile_sync, pg_id,
            contact=contact, request=request, notes=notes, birthday=birthday,
            photo_url=photo_url, object_photos=object_photos, type_code=type_code,
        )

    async def get_object_photos(self, pg_id: int) -> str:
        return await asyncio.to_thread(self._get_object_photos_sync, pg_id)
