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
        pages = await _notion.sessions_by_client(client_id, user_notion_id=user_notion_id)
        return [_parse_history_item(p) for p in pages]

    async def rituals_for(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[HistoryItem]:
        pages = await _notion.rituals_by_client(client_id, user_notion_id=user_notion_id)
        return [_parse_history_item(p) for p in pages]

    async def update_profile(
        self,
        client_id: str,
        *,
        contact: Optional[str] = None,
        request: Optional[str] = None,
        notes: Optional[str] = None,
        birthday: Optional[str] = None,
    ) -> None:
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
        raw_items = await _notion.arcana_all_debts(user_notion_id=user_notion_id)
        result: List[DebtItem] = []
        client_name_cache: dict = {}
        for item in raw_items:
            props = item["properties"]
            amount = _notion._extract_number(props.get("Сумма", {})) or 0.0
            paid = _notion._extract_number(props.get("Оплачено", {})) or 0.0
            debt = amount - paid

            rel = (props.get("Клиент") or {}).get("relation", [])
            client_label = "Личный"
            if rel:
                cid = rel[0]["id"]
                if cid in client_name_cache:
                    client_label = client_name_cache[cid]
                else:
                    try:
                        page = await _notion.get_page(cid)
                        client_label = (
                            _notion._extract_text(page.get("properties", {}).get("Имя", {}))
                            or cid[:8] + "…"
                        )
                    except Exception:
                        client_label = cid[:8] + "…"
                    client_name_cache[cid] = client_label

            desc_prop = props.get("Название", props.get("Вопрос", {}))
            desc = _notion._extract_text(desc_prop)[:40]
            result.append(DebtItem(
                client_label=client_label,
                description=desc,
                debt=debt,
            ))
        return result
