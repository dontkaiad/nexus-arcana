"""arcana/repos/pg_sessions_repo.py — PostgreSQL adapter for 🃏 Расклады (Sessions).

Public methods are async (via asyncio.to_thread); callers receive plain dataclasses.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import select, or_

from arcana.repos.sessions_repo import (
    TripletEntry, PrevSessionSnippet, SessionSearchResult,
)
from arcana.repos.sessions_tables import (
    sessions, session_outcome,
    payment_source as t_payment_source,
    engagement_type,
)
from arcana.repos.clients_tables import clients as t_clients
from core.db import get_engine

logger = logging.getLogger("arcana.pg_sessions")

# ── Code maps ─────────────────────────────────────────────────────────────────

_OUTCOME_TO_CODE = {
    "⏳ не проверено": "unverified",
    "〰️ частично":    "partial",
    "❌ нет":          "no",
    "❌ не сбылось":   "no",
    "✅ да":           "yes",
    "✅ сбылось":      "yes",
    # pass-through
    "unverified": "unverified",
    "partial":    "partial",
    "no":         "no",
    "yes":        "yes",
}

_SESSION_TYPE_TO_CODE = {
    "личный":       "personal",
    "🌟 личный":    "personal",
    "клиентский":   "client",
    "🤝 клиентский":"client",
}

_PAYMENT_TO_CODE = {
    "💳 карта":    "card",
    "💵 наличные": "cash",
    "🔄 бартер":   "barter",
    "карта":       "card",
    "наличные":    "cash",
    "бартер":      "barter",
}


def _code_for(mapping: dict, raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return mapping.get(raw.lower().strip()) or mapping.get(raw.strip())


def _resolve(conn, table, code: Optional[str]) -> Optional[int]:
    if not code:
        return None
    row = conn.execute(
        select(table.c.id).where(table.c.code == code)
    ).fetchone()
    if row is None:
        logger.warning("FK lookup: code %r not found in %s", code, table.name)
    return row[0] if row else None


def _resolve_client_id(conn, client_id: Optional[str]) -> Optional[int]:
    if not client_id:
        return None
    try:
        return int(client_id)
    except (ValueError, TypeError):
        logger.warning("sessions: invalid client_id %r", client_id)
        return None


# ── Row mappers ───────────────────────────────────────────────────────────────

def _row_to_triplet(row) -> TripletEntry:
    cid = str(row.client_id) if row.client_id else None
    d = row.occurred_at
    date_str = d.strftime("%Y-%m-%d") if d else ""
    return TripletEntry(
        id=str(row.id),
        question=row.question or row.title or "",
        cards=row.cards or "",
        interpretation=row.interpretation or "",
        deck=row.deck or "Уэйт",
        session_name=row.session_name or "",
        client_id=cid,
        date=date_str,
        outcome=row.outcome_code or "unverified",
        amount=row.amount or Decimal("0"),
        paid=row.paid or Decimal("0"),
        spread_type=row.spread_type or "",
        area=row.area or "",
        triplet_summary=row.triplet_summary or "",
        session_summary=row.session_summary or "",
        barter_what=row.barter_what or "",
        bottom_card=row.bottom_card or "",
        photo_url=row.photo_url or None,
    )


def _row_to_snippet(row) -> PrevSessionSnippet:
    interp = row.interpretation or ""
    d = row.occurred_at
    date_str = d.strftime("%Y-%m-%d") if d else ""
    return PrevSessionSnippet(
        date=date_str,
        question=row.question or row.title or "",
        cards=row.cards or "",
        interpretation_excerpt=interp[:300] + ("..." if len(interp) > 300 else ""),
    )


def _row_to_search_result(row) -> SessionSearchResult:
    d = row.occurred_at
    date_str = d.strftime("%Y-%m-%d") if d else ""
    cards = row.cards or ""
    return SessionSearchResult(
        date=date_str,
        theme=row.question or row.title or "—",
        spread_name=row.spread_type or "",
        area_name=row.area or "",
        cards_short=(cards[:80] + "…") if len(cards) > 80 else cards,
    )


def _select_sessions():
    return (
        select(
            sessions.c.id,
            sessions.c.title,
            sessions.c.question,
            sessions.c.cards,
            sessions.c.interpretation,
            sessions.c.triplet_summary,
            sessions.c.session_summary,
            sessions.c.bottom_card,
            sessions.c.session_name,
            sessions.c.spread_type,
            sessions.c.area,
            sessions.c.deck,
            sessions.c.occurred_at,
            sessions.c.amount,
            sessions.c.paid,
            sessions.c.barter_what,
            sessions.c.photo_url,
            sessions.c.client_id,
            sessions.c.user_notion_id,
            session_outcome.c.code.label("outcome_code"),
        )
        .outerjoin(session_outcome, sessions.c.outcome_id == session_outcome.c.id)
        .where(sessions.c.archived == False)
        .order_by(sessions.c.occurred_at.desc().nullslast())
    )


# ── Adapter ───────────────────────────────────────────────────────────────────

class PgSessionsRepo:

    def _create_sync(
        self,
        title: str,
        occurred_at: Optional[_date],
        question: str,
        cards: str,
        interpretation: str,
        triplet_summary: str,
        bottom_card: str,
        session_name: str,
        spread_type: str,
        area: str,
        deck: str,
        amount: float,
        paid: float,
        session_type: str,
        payment_source: Optional[str],
        outcome_code: str,
        client_id: Optional[str],
        user_notion_id: str,
    ) -> Optional[str]:
        type_code    = _code_for(_SESSION_TYPE_TO_CODE, session_type)
        pay_code     = _code_for(_PAYMENT_TO_CODE,      payment_source)

        with get_engine().begin() as conn:
            type_id    = _resolve(conn, engagement_type,   type_code)
            pay_id     = _resolve(conn, t_payment_source,  pay_code)
            outcome_id = _resolve(conn, session_outcome,   outcome_code)
            cid_int    = _resolve_client_id(conn, client_id)

            row = conn.execute(
                sessions.insert().values(
                    title=title or question or spread_type or "Сеанс",
                    occurred_at=occurred_at,
                    question=question or None,
                    cards=cards or None,
                    interpretation=interpretation[:4000] if interpretation else None,
                    triplet_summary=triplet_summary[:2000] if triplet_summary else None,
                    bottom_card=bottom_card or None,
                    session_name=session_name or None,
                    spread_type=spread_type or None,
                    area=area or None,
                    deck=deck or None,
                    amount=Decimal(str(amount)) if amount else Decimal("0"),
                    paid=Decimal(str(paid)) if paid else Decimal("0"),
                    type_id=type_id,
                    payment_src_id=pay_id,
                    outcome_id=outcome_id,
                    client_id=cid_int,
                    user_notion_id=user_notion_id or None,
                ).returning(sessions.c.id)
            ).fetchone()
        return str(row[0]) if row else None

    def _find_by_id_sync(self, session_id: str) -> Optional[TripletEntry]:
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return None
        with get_engine().connect() as conn:
            row = conn.execute(
                _select_sessions().where(sessions.c.id == sid)
            ).fetchone()
        return _row_to_triplet(row) if row else None

    def _list_by_client_sync(self, client_id: str) -> List[PrevSessionSnippet]:
        cid_int = _resolve_client_id(None, client_id)
        if cid_int is None:
            return []
        stmt = _select_sessions().where(sessions.c.client_id == cid_int)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_snippet(r) for r in rows]

    def _list_all_sync(
        self,
        user_notion_id: str,
        outcome_filter: Optional[str],
    ) -> List[TripletEntry]:
        stmt = _select_sessions()
        if user_notion_id:
            stmt = stmt.where(sessions.c.user_notion_id == user_notion_id)
        if outcome_filter:
            code = _code_for(_OUTCOME_TO_CODE, outcome_filter) or outcome_filter
            stmt = stmt.where(session_outcome.c.code == code)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_triplet(r) for r in rows]

    def _search_sync(
        self,
        keywords: List[str],
        user_notion_id: str,
        limit: int,
    ) -> List[SessionSearchResult]:
        if not keywords:
            return []
        stmt = _select_sessions()
        if user_notion_id:
            stmt = stmt.where(sessions.c.user_notion_id == user_notion_id)
        filters = [
            or_(
                sessions.c.title.ilike(f"%{kw}%"),
                sessions.c.question.ilike(f"%{kw}%"),
                sessions.c.cards.ilike(f"%{kw}%"),
            )
            for kw in keywords
        ]
        for f in filters:
            stmt = stmt.where(f)
        stmt = stmt.limit(max(1, min(limit, 100)))
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_search_result(r) for r in rows]

    def _update_interp_sync(
        self, session_id: str, interpretation: str, summary: str
    ) -> None:
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return
        vals = {"interpretation": interpretation[:4000] if interpretation else None}
        if summary:
            vals["triplet_summary"] = summary[:2000]
        with get_engine().begin() as conn:
            conn.execute(sessions.update().where(sessions.c.id == sid).values(**vals))

    def _set_outcome_sync(self, session_id: str, outcome_code: str) -> bool:
        code = _code_for(_OUTCOME_TO_CODE, outcome_code) or outcome_code
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            oid = _resolve(conn, session_outcome, code)
            if oid is None:
                logger.warning("set_outcome: unknown code %r", outcome_code)
                return False
            res = conn.execute(
                sessions.update().where(sessions.c.id == sid).values(outcome_id=oid)
            )
        return res.rowcount > 0

    def _set_photo_sync(self, session_id: str, url: str) -> bool:
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            res = conn.execute(
                sessions.update().where(sessions.c.id == sid).values(photo_url=url or None)
            )
        return res.rowcount > 0

    def _update_summary_sync(self, session_id: str, summary: str) -> None:
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return
        with get_engine().begin() as conn:
            conn.execute(
                sessions.update().where(sessions.c.id == sid)
                .values(triplet_summary=summary[:2000] if summary else None)
            )

    def _set_session_summary_sync(self, session_id: str, summary: str) -> bool:
        """Пишет общее саммари сессии на якорный (первый) триплет (#162)."""
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            res = conn.execute(
                sessions.update().where(sessions.c.id == sid)
                .values(session_summary=(summary or None) and summary[:4000])
            )
        return res.rowcount > 0

    def _clear_session_summary_sync(
        self, session_name: str, client_id: Optional[str]
    ) -> int:
        """Сбрасывает session_summary на всех триплетах сессии (стало устаревшим
        после правки/удаления триплета) → миниап предложит регенерацию (#162)."""
        if not session_name:
            return 0
        cond = sessions.c.session_name == session_name
        if client_id:
            try:
                cond = cond & (sessions.c.client_id == int(client_id))
            except (ValueError, TypeError):
                cond = cond & (sessions.c.client_id.is_(None))
        else:
            cond = cond & (sessions.c.client_id.is_(None))
        with get_engine().begin() as conn:
            res = conn.execute(
                sessions.update().where(cond).values(session_summary=None)
            )
        return res.rowcount or 0

    def _list_by_slug_sync(self, slug: str, user_notion_id: str) -> List[TripletEntry]:
        """Load all sessions and filter by slug (session_name__client_id|self)."""
        all_entries = self._list_all_sync(user_notion_id, None)
        from core.session_cache import slugify as _slugify
        result = []
        for e in all_entries:
            if e.session_name:
                entry_slug = f"{_slugify(e.session_name)}__{e.client_id or 'self'}"
            else:
                entry_slug = e.id
            if entry_slug == slug:
                result.append(e)
        return result

    def _archive_sync(self, session_id: str) -> bool:
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            res = conn.execute(
                sessions.update().where(sessions.c.id == sid).values(archived=True)
            )
        return res.rowcount > 0

    def _canonical_session_name_sync(
        self, name: str, client_id: Optional[str], user_notion_id: str
    ) -> str:
        """Return the earliest-used spelling of session_name for merge dedup."""
        if not name:
            return name
        target = name.strip().lower()
        cid_int = _resolve_client_id(None, client_id) if client_id else None
        stmt = (
            select(sessions.c.session_name, sessions.c.occurred_at)
            .where(sessions.c.session_name.ilike(target))
        )
        if cid_int is not None:
            stmt = stmt.where(sessions.c.client_id == cid_int)
        else:
            stmt = stmt.where(sessions.c.client_id.is_(None))
        if user_notion_id:
            stmt = stmt.where(sessions.c.user_notion_id == user_notion_id)
        stmt = stmt.order_by(sessions.c.occurred_at.asc().nullsfirst()).limit(1)
        with get_engine().connect() as conn:
            row = conn.execute(stmt).fetchone()
        return (row[0] or name) if row else name

    # ── Public async interface ────────────────────────────────────────────────

    async def create(
        self,
        title: str = "",
        occurred_at: Optional[_date] = None,
        question: str = "",
        cards: str = "",
        interpretation: str = "",
        triplet_summary: str = "",
        bottom_card: str = "",
        session_name: str = "",
        spread_type: str = "",
        area: str = "",
        deck: str = "",
        amount: float = 0,
        paid: float = 0,
        session_type: str = "Личный",
        payment_source: Optional[str] = None,
        outcome_code: str = "unverified",
        client_id: Optional[str] = None,
        user_notion_id: str = "",
    ) -> Optional[str]:
        return await asyncio.to_thread(
            self._create_sync,
            title, occurred_at, question, cards, interpretation,
            triplet_summary, bottom_card, session_name, spread_type,
            area, deck, amount, paid, session_type, payment_source,
            outcome_code, client_id, user_notion_id,
        )

    async def find_by_id(self, session_id: str) -> Optional[TripletEntry]:
        return await asyncio.to_thread(self._find_by_id_sync, session_id)

    async def list_by_client(self, client_id: str) -> List[PrevSessionSnippet]:
        return await asyncio.to_thread(self._list_by_client_sync, client_id)

    async def list_all(
        self,
        user_notion_id: str = "",
        outcome_filter: Optional[str] = None,
    ) -> List[TripletEntry]:
        return await asyncio.to_thread(
            self._list_all_sync, user_notion_id, outcome_filter
        )

    async def search(
        self,
        keywords: List[str],
        user_notion_id: str = "",
        limit: int = 10,
    ) -> List[SessionSearchResult]:
        return await asyncio.to_thread(
            self._search_sync, keywords, user_notion_id, limit
        )

    async def update_interpretation(
        self, session_id: str, interpretation: str, summary: str = ""
    ) -> None:
        await asyncio.to_thread(
            self._update_interp_sync, session_id, interpretation, summary
        )

    async def set_photo_url(self, session_id: str, url: str) -> bool:
        return await asyncio.to_thread(self._set_photo_sync, session_id, url)

    async def update_summary(self, session_id: str, summary: str) -> None:
        await asyncio.to_thread(self._update_summary_sync, session_id, summary)

    async def set_session_summary(self, session_id: str, summary: str) -> bool:
        return await asyncio.to_thread(
            self._set_session_summary_sync, session_id, summary
        )

    async def clear_session_summary(
        self, session_name: str, client_id: Optional[str]
    ) -> int:
        return await asyncio.to_thread(
            self._clear_session_summary_sync, session_name, client_id
        )

    async def list_by_slug(
        self, slug: str, user_notion_id: str = ""
    ) -> List[TripletEntry]:
        return await asyncio.to_thread(self._list_by_slug_sync, slug, user_notion_id)

    async def set_outcome(self, session_id: str, outcome_code: str) -> bool:
        return await asyncio.to_thread(self._set_outcome_sync, session_id, outcome_code)

    async def archive(self, session_id: str) -> bool:
        return await asyncio.to_thread(self._archive_sync, session_id)

    def _set_work_id_sync(self, session_id: str, work_id: str) -> bool:
        try:
            sid = int(session_id)
            wid = int(work_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            res = conn.execute(
                sessions.update().where(sessions.c.id == sid).values(work_id=wid)
            )
        return res.rowcount > 0

    async def set_work_id(self, session_id: str, work_id: str) -> bool:
        """Привязать расклад к Работе (#151): set work_id."""
        return await asyncio.to_thread(self._set_work_id_sync, session_id, work_id)

    def _set_props_sync(self, session_id: str, fields: dict) -> bool:
        try:
            sid = int(session_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            vals = {}
            q = fields.get("question")
            if q is not None:
                vals["question"] = str(q)
            ar = fields.get("area")
            if ar is not None:
                vals["area"] = str(ar)
            cid = fields.get("client_id")
            if cid is not None:
                resolved = _resolve_client_id(conn, cid)
                if resolved is not None:
                    vals["client_id"] = resolved
            tc = fields.get("type_code")
            if tc:
                tid = _resolve(conn, engagement_type, tc)
                if tid:
                    vals["type_id"] = tid
            app = fields.get("append_interpretation")
            if app:
                row = conn.execute(
                    select(sessions.c.interpretation).where(sessions.c.id == sid)
                ).fetchone()
                cur = (row[0] if row and row[0] else "") or ""
                combined = (cur + "\n" + str(app)).strip() if cur else str(app)
                vals["interpretation"] = combined[:4000]
            if not vals:
                return False
            res = conn.execute(
                sessions.update().where(sessions.c.id == sid).values(**vals)
            )
        return res.rowcount > 0

    async def set_props(self, session_id: str, **fields) -> bool:
        """Обновить поля расклада (reply-правка #156; переиспользуемо #154).

        Поля: question (Тема), area (Область), append_interpretation (Трактовка,
        дописать), client_id + type_code='client' (привязка клиента).
        """
        return await asyncio.to_thread(self._set_props_sync, session_id, fields)

    async def canonical_session_name(
        self, name: str, client_id: Optional[str], user_notion_id: str
    ) -> str:
        return await asyncio.to_thread(
            self._canonical_session_name_sync, name, client_id, user_notion_id
        )
