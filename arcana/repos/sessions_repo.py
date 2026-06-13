"""arcana/repos/sessions_repo.py — domain repository for 🃏 Расклады (Sessions).

All Notion-specific structures (page dicts, prop helpers, raw props building,
direct Notion client calls) are confined here. Callers receive plain dataclasses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from core import notion_client as _notion

logger = logging.getLogger("arcana.sessions_repo")


@dataclass
class TripletEntry:
    """Full representation of one saved triplet/spread page."""
    id: str
    question: str           # from "Тема" title
    cards: str              # from "Карты" rich_text
    interpretation: str     # from "Трактовка" rich_text
    deck: str               # from "Колоды" multi_select (joined)
    session_name: str       # from "Сессия" rich_text
    client_id: Optional[str]  # first entry in "👥 Клиенты" relation, or None


@dataclass
class PrevSessionSnippet:
    """Lightweight view of a past session — used to build context for Sonnet prompts."""
    date: str
    question: str
    cards: str
    interpretation_excerpt: str  # first 300 chars of "Трактовка"


@dataclass
class SessionSearchResult:
    """One hit from keyword search — used in handle_session_search display."""
    date: str
    theme: str
    spread_name: str
    area_name: str
    cards_short: str


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_triplet_entry(page: dict) -> TripletEntry:
    props = page.get("properties", {})
    title_parts = props.get("Тема", {}).get("title", [])
    question = title_parts[0].get("plain_text", "") if title_parts else ""
    cards = "".join(
        x.get("plain_text", "") for x in props.get("Карты", {}).get("rich_text") or []
    ).strip()
    interpretation = "".join(
        x.get("plain_text", "") for x in props.get("Трактовка", {}).get("rich_text") or []
    ).strip()
    deck_list = [it.get("name", "") for it in props.get("Колоды", {}).get("multi_select") or []]
    deck = ", ".join(deck_list) or "Уэйт"
    session_name = "".join(
        x.get("plain_text", "") for x in props.get("Сессия", {}).get("rich_text") or []
    ).strip()
    cids = [r.get("id", "") for r in props.get("👥 Клиенты", {}).get("relation", [])]
    return TripletEntry(
        id=page.get("id", ""),
        question=question,
        cards=cards,
        interpretation=interpretation,
        deck=deck,
        session_name=session_name,
        client_id=cids[0] if cids else None,
    )


def _parse_prev_snippet(page: dict) -> PrevSessionSnippet:
    props = page.get("properties", {})
    date_prop = props.get("Дата и время") or props.get("Дата") or {}
    date_val = date_prop.get("date") or {}
    date = (date_val.get("start") or "")[:10]
    question = _notion._extract_text(props.get("Тема") or {})
    cards = _notion._extract_text(props.get("Карты") or {})
    interp = _notion._extract_text(props.get("Трактовка") or {})
    return PrevSessionSnippet(
        date=date,
        question=question,
        cards=cards,
        interpretation_excerpt=interp[:300] + ("..." if len(interp) > 300 else ""),
    )


def _parse_search_result(page: dict) -> SessionSearchResult:
    props = page.get("properties", {})
    date_prop = props.get("Дата") or props.get("Дата и время") or {}
    date_val = date_prop.get("date") or {}
    date = (date_val.get("start") or "")[:10]
    theme = _notion._extract_text(props.get("Тема") or {}) or "—"
    spread_items = (props.get("Тип расклада") or {}).get("multi_select") or []
    spread_name = spread_items[0].get("name", "") if spread_items else ""
    area_prop = props.get("Область") or {}
    area_items = area_prop.get("multi_select") or []
    if area_items:
        area_name = area_items[0].get("name", "")
    else:
        area_name = (area_prop.get("select") or {}).get("name", "")
    cards_text = _notion._extract_text(props.get("Карты") or {})
    cards_short = (cards_text[:80] + "…") if len(cards_text) > 80 else cards_text
    return SessionSearchResult(
        date=date,
        theme=theme,
        spread_name=spread_name,
        area_name=area_name,
        cards_short=cards_short,
    )


# ── Repository ────────────────────────────────────────────────────────────────

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
        return await _notion.session_add(
            date=date,
            spread_type=spread_type,
            question=question,
            cards=cards,
            interpretation=interpretation,
            amount=amount,
            paid=paid,
            session_type=session_type,
            client_id=client_id,
            user_notion_id=user_notion_id,
            area=area,
            deck=deck,
            payment_source=payment_source,
            title=title,
            session=session,
            triplet_summary=triplet_summary,
            bottom_card=bottom_card,
        )

    async def prev_for_client(
        self, client_id: str, user_notion_id: str = ""
    ) -> List[PrevSessionSnippet]:
        pages = await _notion.sessions_by_client(client_id, user_notion_id=user_notion_id)
        return [_parse_prev_snippet(p) for p in pages]

    async def search(
        self,
        keywords: List[str],
        user_notion_id: str = "",
        limit: int = 10,
    ) -> List[SessionSearchResult]:
        pages = await _notion.sessions_search(
            keywords, user_notion_id=user_notion_id, limit=limit
        )
        return [_parse_search_result(p) for p in pages]

    async def find_by_short_id(
        self, short_id: str, user_notion_id: str = ""
    ) -> Optional[TripletEntry]:
        """short_id (32 hex без дефисов) → TripletEntry. None если не найден
        или принадлежит чужому пользователю."""
        pages = await _notion.sessions_all(user_notion_id=user_notion_id)
        for p in pages:
            pid = p.get("id", "").replace("-", "")
            if pid.startswith(short_id) or short_id.startswith(pid[:32]):
                return _parse_triplet_entry(p)
        return None

    async def update_interpretation(
        self, page_id: str, interpretation: str, summary: str = ""
    ) -> None:
        """Обновить поля Трактовка + (опц.) Саммари триплета."""
        try:
            await _notion.update_page(
                page_id, {"Трактовка": _notion._text(interpretation[:2000])}
            )
        except Exception as e:
            logger.error("update_interpretation failed: %s", e)
        if summary:
            try:
                await _notion.update_page(
                    page_id, {"Саммари триплета": _notion._text(summary[:1800])}
                )
            except Exception as e:
                logger.warning("update summary failed: %s", e)

    async def archive(self, page_id: str) -> bool:
        """Архивировать страницу (удаление через Notion API)."""
        try:
            await _notion.get_notion().pages.update(page_id=page_id, archived=True)
            return True
        except Exception as e:
            logger.error("archive %s failed: %s", page_id, e)
            return False
