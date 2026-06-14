"""arcana/repos/clients_repo.py — domain repository for 👥 Клиенты.

Notion-specific structures (page dicts, prop helpers, raw props building)
are fully contained here. Callers receive plain dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from core import notion_client as _notion

# Re-export Notion select values so handlers don't import notion_client directly.
CLIENT_TYPE_PAID: str = _notion.CLIENT_TYPE_PAID
CLIENT_TYPE_FREE: str = _notion.CLIENT_TYPE_FREE


def _pg_clients():
    from arcana.repos.pg_clients_repo import PgClientsRepo
    return PgClientsRepo()


def _pg_rituals():
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    return PgRitualsRepo()


@dataclass
class Client:
    id: str
    name: str
    contact: str
    request: str
    notes: str
    since: str  # "YYYY-MM-DD" or ""


@dataclass
class HistoryItem:
    """One session or ritual record for a client — used in handle_client_info."""
    amount: float
    paid: float
    description: str
    date: str  # "YYYY-MM-DD" or ""


@dataclass
class DebtItem:
    client_label: str  # resolved client name or "Личный"
    description: str
    debt: float


def _parse_client(page: dict) -> Client:
    props = page["properties"]
    since_date = (props.get("Первое обращение") or {}).get("date") or {}
    return Client(
        id=page["id"],
        name=_notion._extract_text(props.get("Имя", {})),
        contact=_notion._extract_text(props.get("Контакт", {})),
        request=_notion._extract_text(props.get("Запрос", {})),
        notes=_notion._extract_text(props.get("Заметки", {})),
        since=(since_date.get("start") or "")[:10],
    )


def _parse_history_item(page: dict) -> HistoryItem:
    props = page["properties"]
    amount = _notion._extract_number(props.get("Сумма", {})) or 0.0
    paid = _notion._extract_number(props.get("Оплачено", {})) or 0.0
    # sessions use "Вопрос", rituals use "Название"
    desc_prop = props.get("Вопрос", props.get("Название", {}))
    desc = _notion._extract_text(desc_prop)
    date_val = ((props.get("Дата и время") or props.get("Дата") or {}).get("date") or {})
    date = (date_val.get("start") or "")[:10]
    return HistoryItem(amount=amount, paid=paid, description=desc, date=date)


class ClientsRepo:
    async def find(
        self, name: str, user_notion_id: str = ""
    ) -> Optional[Client]:
        # PG primary; Notion fallback for clients not yet synced
        client = await _pg_clients().find(name)
        if client:
            return client
        page = await _notion.client_find(name, user_notion_id=user_notion_id)
        return _parse_client(page) if page else None

    async def add(
        self,
        name: str,
        contact: str = "",
        request: str = "",
        date: str = "",
        user_notion_id: str = "",
        client_type: Optional[str] = None,
    ) -> Optional[str]:
        # Create in Notion (source of truth for profile writes); PG sync via find_or_create hook
        return await _notion.client_add(
            name=name,
            contact=contact,
            request=request,
            date=date or None,
            user_notion_id=user_notion_id,
            client_type=client_type,
        )

    async def sessions_for(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[HistoryItem]:
        # Sessions stay in Notion
        pages = await _notion.sessions_by_client(client_id, user_notion_id=user_notion_id)
        return [_parse_history_item(p) for p in pages]

    async def rituals_for(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[HistoryItem]:
        # PG rituals; client_id may be Notion UUID — PgRitualsRepo resolves internally
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
    ) -> None:
        # Write to Notion for backward compat (client_id may be Notion UUID)
        props = {}
        if contact and contact != "—":
            props["Контакт"] = _notion._text(contact)
        if request:
            props["Запрос"] = _notion._text(request)
        if notes:
            props["Заметки"] = _notion._text(notes)
        if birthday:
            props["День рождения"] = {"date": {"start": birthday}}
        if props:
            try:
                await _notion.update_page(client_id, props)
            except Exception as e:
                import logging
                logging.getLogger("arcana.clients_repo").warning("update_profile: %s", e)

    async def all_debts(
        self, user_notion_id: str = ""
    ) -> List[DebtItem]:
        result: List[DebtItem] = []

        # ── PG: rituals with debt ─────────────────────────────────────────────
        pg_client_cache: dict = {}
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
                if cid_int in pg_client_cache:
                    client_label = pg_client_cache[cid_int]
                else:
                    c = await _pg_clients().find_by_id(cid_int)
                    client_label = c.name if c else f"#{cid_int}"
                    pg_client_cache[cid_int] = client_label
            result.append(DebtItem(
                client_label=client_label,
                description=(ritual.name or "")[:40],
                debt=debt,
            ))

        # ── Notion: sessions with debt (bridge) ───────────────────────────────
        raw_sessions = await _notion.sessions_all(user_notion_id=user_notion_id)
        notion_client_cache: dict = {}
        for item in raw_sessions:
            props = item["properties"]
            amount = _notion._extract_number(props.get("Сумма", {})) or 0.0
            paid_val = _notion._extract_number(props.get("Оплачено", {})) or 0.0
            if amount - paid_val <= 0:
                continue
            rel = (props.get("Клиент") or {}).get("relation", [])
            client_label = "Личный"
            if rel:
                cid = rel[0]["id"]
                if cid in notion_client_cache:
                    client_label = notion_client_cache[cid]
                else:
                    try:
                        page = await _notion.get_page(cid)
                        client_label = (
                            _notion._extract_text(page.get("properties", {}).get("Имя", {}))
                            or cid[:8] + "…"
                        )
                    except Exception:
                        client_label = cid[:8] + "…"
                    notion_client_cache[cid] = client_label
            desc_prop = props.get("Вопрос", props.get("Название", {}))
            desc = _notion._extract_text(desc_prop)[:40]
            result.append(DebtItem(
                client_label=client_label,
                description=desc,
                debt=amount - paid_val,
            ))

        return result
