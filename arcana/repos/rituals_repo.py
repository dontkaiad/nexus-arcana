"""arcana/repos/rituals_repo.py — domain repository for 🕯️ Ритуалы.

Notion-specific structures (page dicts, prop helpers, select matching)
are fully contained here. Callers receive plain Ritual dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core import notion_client as _notion


@dataclass
class Ritual:
    id: str                          # stable identifier (Notion page_id now; Postgres pk later)
    name: str
    date: Optional[datetime] = None
    client_id: Optional[str] = None
    result: Optional[str] = None     # outcome code: 'unverified'|'partial'|'negative'|'positive'
    price: Optional[Decimal] = None
    paid: Decimal = field(default_factory=lambda: Decimal("0"))
    goal: Optional[str] = None       # magical purpose code (e.g. 'finance', 'love')
    place: Optional[str] = None      # ritual_place code (e.g. 'home', 'forest')


def goal_label(goal: str) -> str:
    """Return display label for a ritual goal key (e.g. 'финансы' → '💰 Финансы')."""
    return _notion._RITUAL_GOAL_MAP.get(goal.lower(), goal)


def place_label(place: str) -> str:
    """Return display label for a ritual place key (e.g. 'дома' → '🏠 Дома')."""
    return _notion._RITUAL_PLACE_MAP.get(place.lower(), place)


class RitualsRepo:
    async def create(
        self,
        name: str,
        date: str,
        ritual_type: str = "Личный",
        consumables: str = "",
        consumables_cost: float = 0,
        duration_min: float = 0,
        offerings: str = "",
        forces: str = "",
        structure: str = "",
        amount: float = 0,
        paid: float = 0,
        client_id: Optional[str] = None,
        user_notion_id: str = "",
        goal: Optional[str] = None,
        place: Optional[str] = None,
        notes: Optional[str] = None,
        payment_source: Optional[str] = None,
        offerings_cost: Optional[float] = None,
    ) -> Optional[Ritual]:
        page_id = await _notion.ritual_add(
            name=name,
            date=date,
            ritual_type=ritual_type,
            consumables=consumables,
            consumables_cost=consumables_cost,
            duration_min=duration_min,
            offerings=offerings,
            forces=forces,
            structure=structure,
            amount=amount,
            paid=paid,
            client_id=client_id,
            user_notion_id=user_notion_id,
            goal=goal,
            place=place,
            notes=notes,
            payment_source=payment_source,
            offerings_cost=offerings_cost,
        )
        if page_id is None:
            return None
        return Ritual(
            id=page_id,
            name=name,
            paid=Decimal(str(paid)),
            price=Decimal(str(amount)) if amount else None,
            client_id=client_id,
            goal=goal,
            place=place,
        )
