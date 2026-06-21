"""arcana/repos/sessions_repo.py — domain repository for 🃏 Расклады (Sessions).

Pure PG — no Notion calls. Callers receive plain dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal
from typing import List, Optional


@dataclass
class TripletEntry:
    id: str
    question: str
    cards: str
    interpretation: str
    deck: str
    session_name: str
    client_id: Optional[str]
    date: str = ""            # "YYYY-MM-DD" or ""
    outcome: str = ""         # PG code: yes/no/partial/unverified
    amount: Decimal = field(default_factory=lambda: Decimal("0"))
    paid: Decimal = field(default_factory=lambda: Decimal("0"))
    spread_type: str = ""     # Тип расклада
    area: str = ""            # Область
    triplet_summary: str = "" # Саммари / AI_Summary
    barter_what: str = ""     # Бартер · что
    bottom_card: str = ""     # Дно колоды
    photo_url: Optional[str] = None


@dataclass
class PrevSessionSnippet:
    date: str
    question: str
    cards: str
    interpretation_excerpt: str


@dataclass
class SessionSearchResult:
    date: str
    theme: str
    spread_name: str
    area_name: str
    cards_short: str


def _pg_repo():
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    return PgSessionsRepo()


class SessionsRepo:
    async def add(
        self,
        date: str,
        spread_type: str = "",
        question: str = "",
        cards: str = "",
        interpretation: str = "",
        amount: float = 0,
        paid: float = 0,
        session_type: str = "Личный",
        client_id: Optional[str] = None,
        user_notion_id: str = "",
        area: Optional[str] = None,
        deck: Optional[str] = None,
        payment_source: Optional[str] = None,
        title: Optional[str] = None,
        session: Optional[str] = None,
        triplet_summary: Optional[str] = None,
        bottom_card: Optional[str] = None,
    ) -> Optional[str]:
        # Resolve canonical session name for multi-triplet grouping
        session_name = session or ""
        if session_name:
            session_name = await _pg_repo().canonical_session_name(
                session_name, client_id, user_notion_id
            )

        occurred_at: Optional[_date] = None
        if date:
            try:
                occurred_at = _date.fromisoformat(date[:10])
            except ValueError:
                pass

        return await _pg_repo().create(
            title=title or question or spread_type or "Сеанс",
            occurred_at=occurred_at,
            question=question,
            cards=cards,
            interpretation=interpretation,
            triplet_summary=triplet_summary or "",
            bottom_card=bottom_card or "",
            session_name=session_name,
            spread_type=spread_type,
            area=area or "",
            deck=deck or "",
            amount=amount,
            paid=paid,
            session_type=session_type,
            payment_source=payment_source,
            outcome_code="unverified",
            client_id=client_id,
            user_notion_id=user_notion_id,
        )

    async def prev_for_client(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[PrevSessionSnippet]:
        return await _pg_repo().list_by_client(client_id)

    async def search(
        self,
        keywords: List[str],
        user_notion_id: str = "",
        limit: int = 10,
    ) -> List[SessionSearchResult]:
        return await _pg_repo().search(keywords, user_notion_id=user_notion_id, limit=limit)

    async def find_by_short_id(
        self, short_id: str, user_notion_id: str = ""
    ) -> Optional[TripletEntry]:
        """short_id = str(pg_id) after PG migration."""
        return await _pg_repo().find_by_id(short_id)

    async def update_interpretation(
        self, page_id: str, interpretation: str, summary: str = ""
    ) -> None:
        await _pg_repo().update_interpretation(page_id, interpretation, summary)

    async def archive(self, page_id: str) -> bool:
        return await _pg_repo().archive(page_id)

    async def set_photo_url(self, page_id: str, url: str) -> bool:
        return await _pg_repo().set_photo_url(page_id, url)
