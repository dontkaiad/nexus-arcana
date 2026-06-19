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
    id: str                          # stable identifier (Postgres pk)
    name: str
    date: Optional[datetime] = None
    client_id: Optional[str] = None
    result: Optional[str] = None     # outcome code: 'unverified'|'partial'|'negative'|'positive'
    price: Optional[Decimal] = None
    paid: Decimal = field(default_factory=lambda: Decimal("0"))
    goal: Optional[str] = None       # magical purpose code (e.g. 'finance', 'love')
    place: Optional[str] = None      # ritual_place code (e.g. 'home', 'forest')
    # Extended fields populated when reading from PG (miniapp detail view)
    type_code: Optional[str] = None  # engagement_type code: 'personal'|'client'
    time_min: Optional[int] = None
    consumables: str = ""
    offerings: str = ""
    powers: str = ""
    structure: str = ""
    notes: Optional[str] = None
    photo_url: Optional[str] = None
    payment_source: Optional[str] = None  # display label, e.g. "💵 Наличные"
    barter_what: str = ""


def goal_label(goal: str) -> str:
    """Return display label for a ritual goal key (e.g. 'финансы' → '💰 Финансы')."""
    return _notion._RITUAL_GOAL_MAP.get(goal.lower(), goal)


def place_label(place: str) -> str:
    """Return display label for a ritual place key (e.g. 'дома' → '🏠 Дома')."""
    return _notion._RITUAL_PLACE_MAP.get(place.lower(), place)


