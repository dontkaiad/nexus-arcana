"""arcana/repos/clients_repo.py — domain repository for 👥 Клиенты.

Pure PG — no Notion calls. Callers receive plain dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Keep re-exports so handlers that import these constants keep working.
CLIENT_TYPE_PAID: str = "🤝 Платный"
CLIENT_TYPE_FREE: str = "🎁 Бесплатный"


def _pg_clients():
    from arcana.repos.pg_clients_repo import PgClientsRepo
    return PgClientsRepo()


def _pg_rituals():
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    return PgRitualsRepo()


def _pg_sessions():
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    return PgSessionsRepo()


@dataclass
class Client:
    id: str        # str(pg_id)
    name: str
    contact: str
    request: str
    notes: str
    since: str
    type_code: Optional[str] = None    # "paid" / "free" / "self"
    status_code: Optional[str] = None  # "active" / "one_time" / "closed"
    birthday: Optional[str] = None     # "YYYY-MM-DD"
    photo_url: Optional[str] = None
    object_photos: Optional[str] = None  # raw "URL | note\n..." string


@dataclass
class HistoryItem:
    amount: float
    paid: float
    description: str
    date: str


@dataclass
class DebtItem:
    client_label: str
    description: str
    debt: float


class ClientsRepo:
    async def find(
        self, name: str, user_notion_id: str = ""
    ) -> Optional[Client]:
        return await _pg_clients().find(name)

    async def add(
        self,
        name: str,
        contact: str = "",
        request: str = "",
        date: str = "",
        user_notion_id: str = "",
        client_type: Optional[str] = None,
    ) -> Optional[str]:
        # Map Notion display labels to PG codes
        _type_map = {
            "🤝 Платный":   "paid",
            "🎁 Бесплатный":"free",
            "🌟 Self":       "self",
            "Платный":       "paid",
            "Бесплатный":    "free",
        }
        type_code = _type_map.get(client_type or "", "paid")
        pg_id = await _pg_clients().create(name=name, type_code=type_code, contact=contact, request=request)
        return str(pg_id) if pg_id else None

    async def sessions_for(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[HistoryItem]:
        snippets = await _pg_sessions().list_by_client(client_id)
        return [
            HistoryItem(
                amount=0.0,
                paid=0.0,
                description=s.question[:40] if s.question else "",
                date=s.date,
            )
            for s in snippets
        ]

    async def rituals_for(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[HistoryItem]:
        rituals = await _pg_rituals().list_by_client(client_id)
        return [
            HistoryItem(
                amount=float(r.price or 0),
                paid=float(r.paid),
                description=r.name[:40] if r.name else "",
                date=r.date.strftime("%Y-%m-%d") if r.date else "",
            )
            for r in rituals
        ]

    async def update_profile(
        self,
        client_id: str,
        *,
        contact: Optional[str] = None,
        request: Optional[str] = None,
        notes: Optional[str] = None,
        birthday: Optional[str] = None,
        type_code: Optional[str] = None,
    ) -> None:
        try:
            pg_id = int(client_id)
        except (ValueError, TypeError):
            return
        await _pg_clients().update_profile(
            pg_id,
            contact=contact,
            request=request,
            notes=notes,
            birthday=birthday,
            type_code=type_code,
        )

    async def list_all(self, user_notion_id: str = "") -> List[Client]:
        return await _pg_clients().list_all(user_notion_id)

    async def find_by_id(self, client_id: str) -> Optional[Client]:
        try:
            pg_id = int(client_id)
        except (ValueError, TypeError):
            return None
        return await _pg_clients().find_by_id(pg_id)

    async def get_object_photos(self, client_id: str) -> str:
        try:
            pg_id = int(client_id)
        except (ValueError, TypeError):
            return ""
        return await _pg_clients().get_object_photos(pg_id)

    async def update_object_photos(self, client_id: str, raw: str) -> None:
        try:
            pg_id = int(client_id)
        except (ValueError, TypeError):
            return
        await _pg_clients().update_profile(pg_id, object_photos=raw)

    async def update_photo_url(self, client_id: str, url: str) -> None:
        try:
            pg_id = int(client_id)
        except (ValueError, TypeError):
            return
        await _pg_clients().update_profile(pg_id, photo_url=url)

    async def all_debts(
        self, user_notion_id: str = ""
    ) -> List[DebtItem]:
        result: List[DebtItem] = []
        pg_client_cache: dict = {}

        # Rituals
        all_rituals = await _pg_rituals().list_all()
        for ritual in all_rituals:
            if ritual.price is None:
                continue
            debt = float(ritual.price) - float(ritual.paid)
            if debt <= 0:
                continue
            client_label = "Личный"
            if ritual.client_id:
                cid_int = int(ritual.client_id)
                if cid_int not in pg_client_cache:
                    c = await _pg_clients().find_by_id(cid_int)
                    pg_client_cache[cid_int] = c.name if c else f"#{cid_int}"
                client_label = pg_client_cache[cid_int]
            result.append(DebtItem(
                client_label=client_label,
                description=(ritual.name or "")[:40],
                debt=debt,
            ))

        # Sessions
        all_sessions = await _pg_sessions().list_all(user_notion_id=user_notion_id)
        for s in all_sessions:
            # Sessions currently don't store amount/paid — skip debt for now
            pass

        return result
