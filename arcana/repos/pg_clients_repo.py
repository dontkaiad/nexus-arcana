"""arcana/repos/pg_clients_repo.py — PostgreSQL adapter for 👥 Клиенты.

Methods derived from actual call-sites only:
  find(name)           — ILIKE search, used in ClientsRepo.find + handle_client_info
  find_by_id(pg_id)    — PK lookup, used in all_debts to resolve client_label
  get_pg_id_for_notion(notion_uuid) — used inside PgRitualsRepo to resolve FK
  sync_notion_client(notion_uuid, name, type_code) — upsert called from find_or_create_client

Client.id stores str(pg_id) so existing callers that do client["id"] / client.id get an integer
string instead of Notion UUID. Notion-write callers that need the original UUID use notion_id.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import select

from arcana.repos.clients_repo import Client
from arcana.repos.clients_tables import client_type, client_status, clients
from core.db import get_engine

logger = logging.getLogger("arcana.pg_clients")

# Notion display label → PG code (tolerant mapping)
_NOTION_TYPE_TO_CODE = {
    "🎁 бесплатный": "free",
    "🤝 платный":    "paid",
    "🌟 self":        "self",
    # bare words
    "бесплатный": "free",
    "платный":    "paid",
    "self":        "self",
    # pass-through
    "free": "free",
    "paid": "paid",
}

_NOTION_STATUS_TO_CODE = {
    "⛔ закрытый": "closed",
    "🌙 разовый":  "one_time",
    "🟢 активный": "active",
    # bare
    "закрытый": "closed",
    "разовый":  "one_time",
    "активный": "active",
    # pass-through
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
        with get_engine().connect() as conn:
            row = conn.execute(
                select(clients).where(clients.c.id == pg_id)
            ).fetchone()
        return _row_to_client(row) if row else None

    def _get_pg_id_for_notion_sync(self, notion_uuid: str) -> Optional[int]:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(clients.c.id).where(clients.c.notion_id == notion_uuid)
            ).fetchone()
        return row[0] if row else None

    def _sync_notion_client_sync(
        self,
        notion_uuid: str,
        name: str,
        type_code: Optional[str],
        status_code: Optional[str] = "active",
        contact: Optional[str] = None,
        notes: Optional[str] = None,
        request: Optional[str] = None,
    ) -> int:
        """Insert client if notion_id not present; return PG pk."""
        with get_engine().begin() as conn:
            # Check existing
            existing = conn.execute(
                select(clients.c.id).where(clients.c.notion_id == notion_uuid)
            ).fetchone()
            if existing:
                return existing[0]

            type_id   = _resolve_lookup(conn, client_type,   type_code or "paid")
            status_id = _resolve_lookup(conn, client_status, status_code or "active")
            row = conn.execute(
                clients.insert().values(
                    notion_id=notion_uuid,
                    name=name,
                    type_id=type_id,
                    status_id=status_id,
                    contact=contact or None,
                    notes=notes or None,
                    request=request or None,
                ).returning(clients.c.id)
            ).fetchone()
        return row[0]

    def _update_profile_sync(
        self,
        pg_id: int,
        *,
        contact: Optional[str],
        request: Optional[str],
        notes: Optional[str],
        birthday: Optional[str],
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
        if not vals:
            return
        with get_engine().begin() as conn:
            conn.execute(clients.update().where(clients.c.id == pg_id).values(**vals))

    # ── Public async interface ────────────────────────────────────────────────

    async def find(self, name: str) -> Optional[Client]:
        return await asyncio.to_thread(self._find_sync, name)

    async def find_by_id(self, pg_id: int) -> Optional[Client]:
        return await asyncio.to_thread(self._find_by_id_sync, pg_id)

    async def get_pg_id_for_notion(self, notion_uuid: str) -> Optional[int]:
        return await asyncio.to_thread(self._get_pg_id_for_notion_sync, notion_uuid)

    async def sync_notion_client(
        self,
        notion_uuid: str,
        name: str,
        type_code: Optional[str],
        status_code: Optional[str] = "active",
        contact: Optional[str] = None,
        notes: Optional[str] = None,
        request: Optional[str] = None,
    ) -> int:
        return await asyncio.to_thread(
            self._sync_notion_client_sync,
            notion_uuid, name, type_code, status_code, contact, notes, request,
        )

    async def update_profile(
        self,
        pg_id: int,
        *,
        contact: Optional[str] = None,
        request: Optional[str] = None,
        notes: Optional[str] = None,
        birthday: Optional[str] = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_profile_sync, pg_id,
            contact=contact, request=request, notes=notes, birthday=birthday,
        )
