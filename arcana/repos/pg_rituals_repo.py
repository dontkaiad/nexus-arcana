"""arcana/repos/pg_rituals_repo.py — PostgreSQL adapter for 🕯️ Ритуалы.

Uses SQLAlchemy Core (synchronous, psycopg2).  Callers receive Ritual dataclasses.
Notion adapter (rituals_repo.py) is unchanged — this is a parallel implementation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.engine import Connection

from arcana.repos.rituals_repo import Ritual
from arcana.repos.rituals_tables import (
    engagement_type,
    magical_purpose,
    outcome_status,
    payment_source as t_payment_source,
    ritual_place,
    rituals,
)
from core.db import get_engine

logger = logging.getLogger("arcana.pg_rituals")

# ── Label → code maps (callers pass Russian labels; PG stores codes) ──────────

# ritual_type: "Личный" / "Клиентский" (from handler)
_TYPE_TO_CODE = {
    "личный":      "personal",
    "клиентский":  "client",
    "клиентская":  "client",  # gender drift tolerance
}

# goal: lowercase Russian (from LLM parser)
_GOAL_TO_CODE = {
    "привлечение": "attract",
    "защита":      "protect",
    "очищение":    "cleanse",
    "любовь":      "love",
    "финансы":     "finance",
    "деструктив":  "destruct_return",
    "развязка":    "cut_off",
    "приворот":    "love_bind",
    "другое":      "other",
}

# place: lowercase Russian (from LLM parser)
_PLACE_TO_CODE = {
    "дома":         "home",
    "лес":          "forest",
    "погост":       "graveyard",
    "перекрёсток":  "crossroad",
    "церковь":      "church",
    "водоём":       "water",
    "поле":         "field",
    "другое":       "other",
}

# payment_source: display label with emoji (from PAYMENT_SOURCE_MAP)
_PAYMENT_TO_CODE = {
    "💳 карта":    "card",
    "💵 наличные": "cash",
    "🔄 бартер":   "barter",
    # bare words (defensive)
    "карта":       "card",
    "наличные":    "cash",
    "бартер":      "barter",
}

# result: "⏳ Не проверено" default; also accept codes directly
_RESULT_TO_CODE = {
    "⏳ не проверено": "unverified",
    "〰️ частично":    "partial",
    "❌ не сработал":  "negative",
    "✅ сработал":     "positive",
    # pass-through if already a code
    "unverified": "unverified",
    "partial":    "partial",
    "negative":   "negative",
    "positive":   "positive",
}


# ── FK resolution helpers ─────────────────────────────────────────────────────

def _resolve(
    conn: Connection,
    table,
    code: Optional[str],
) -> Optional[int]:
    """Return the integer PK for `code` in `table`, or None if missing/unknown."""
    if not code:
        return None
    row = conn.execute(
        select(table.c.id).where(table.c.code == code)
    ).fetchone()
    if row is None:
        logger.warning("FK lookup: code %r not found in %s", code, table.name)
        return None
    return row.id


def _code_for(mapping: dict, raw: Optional[str]) -> Optional[str]:
    """Map a raw caller-supplied label to a PG code string."""
    if not raw:
        return None
    return mapping.get(raw.lower().strip()) or mapping.get(raw.strip())


# ── Row → Ritual ──────────────────────────────────────────────────────────────

def _row_to_ritual(row) -> Ritual:
    """Convert a SELECT row (with joined lookup columns) to a Ritual dataclass."""
    return Ritual(
        id=str(row.id),
        name=row.title,
        date=row.occurred_at,
        client_id=str(row.client_id) if row.client_id else None,
        result=row.outcome_code,
        price=row.price,
        paid=row.paid if row.paid is not None else Decimal("0"),
        goal=row.purpose_code,
        place=row.place_code,
    )


# ── Base SELECT with all joins (DRY) ─────────────────────────────────────────

def _select_rituals():
    """Base query: rituals + joined lookup codes."""
    oc = outcome_status.alias("oc")
    mp = magical_purpose.alias("mp")
    rp = ritual_place.alias("rp")
    return (
        select(
            rituals.c.id,
            rituals.c.title,
            rituals.c.occurred_at,
            rituals.c.client_id,
            rituals.c.price,
            rituals.c.paid,
            oc.c.code.label("outcome_code"),
            mp.c.code.label("purpose_code"),
            rp.c.code.label("place_code"),
        )
        .outerjoin(oc,  rituals.c.outcome_id  == oc.c.id)
        .outerjoin(mp,  rituals.c.purpose_id  == mp.c.id)
        .outerjoin(rp,  rituals.c.place_id    == rp.c.id)
        .order_by(rituals.c.occurred_at.desc().nullslast())
    )


# ── Public adapter ────────────────────────────────────────────────────────────

class PgRitualsRepo:
    """PostgreSQL adapter for the rituals domain.

    Drop-in replacement for the Notion-backed RitualsRepo once cutover happens.
    Uses synchronous SQLAlchemy Core (same psycopg2 driver as Alembic).
    """

    def create(
        self,
        name: str,
        date: str,                          # "YYYY-MM-DD" from handler
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
        user_notion_id: str = "",           # ignored (ADR-0007 — single owner)
        goal: Optional[str] = None,
        place: Optional[str] = None,
        notes: Optional[str] = None,
        payment_source: Optional[str] = None,
        offerings_cost: Optional[float] = None,
    ) -> Optional[Ritual]:
        """Insert a ritual row; resolve all lookup FK ids from caller labels."""
        type_code    = _code_for(_TYPE_TO_CODE,    ritual_type)
        goal_code    = _code_for(_GOAL_TO_CODE,    goal)
        place_code   = _code_for(_PLACE_TO_CODE,   place)
        pay_code     = _code_for(_PAYMENT_TO_CODE, payment_source)
        result_code  = "unverified"  # default for new rituals

        occurred_at: Optional[datetime] = None
        if date:
            try:
                occurred_at = datetime.strptime(date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning("create: bad date %r — storing NULL", date)

        with get_engine().begin() as conn:
            type_id   = _resolve(conn, engagement_type, type_code)
            purpose_id = _resolve(conn, magical_purpose, goal_code)
            place_id  = _resolve(conn, ritual_place,    place_code)
            pay_id    = _resolve(conn, t_payment_source, pay_code)
            outcome_id = _resolve(conn, outcome_status,  result_code)

            client_id_int = int(client_id) if client_id and client_id.isdigit() else None

            row = conn.execute(
                rituals.insert().values(
                    title=name,
                    occurred_at=occurred_at,
                    client_id=client_id_int,
                    type_id=type_id,
                    purpose_id=purpose_id,
                    place_id=place_id,
                    payment_src_id=pay_id,
                    outcome_id=outcome_id,
                    price=Decimal(str(amount)) if amount else None,
                    paid=Decimal(str(paid)),
                    offerings_sum=Decimal(str(offerings_cost)) if offerings_cost else None,
                    duration_min=int(duration_min) if duration_min else None,
                    forces=forces or None,
                    structure=structure or None,
                    consumables=consumables or None,
                    offerings=offerings or None,
                    notes=notes or None,
                ).returning(rituals.c.id)
            ).fetchone()

        if row is None:
            return None

        return Ritual(
            id=str(row.id),
            name=name,
            date=occurred_at,
            client_id=client_id,
            result=result_code,
            price=Decimal(str(amount)) if amount else None,
            paid=Decimal(str(paid)),
            goal=goal_code,
            place=place_code,
        )

    def list_by_client(
        self,
        client_id: str,
        user_notion_id: str = "",
    ) -> List[Ritual]:
        """All rituals for a client, sorted date DESC."""
        client_id_int = int(client_id) if client_id and client_id.isdigit() else None
        if client_id_int is None:
            return []
        stmt = _select_rituals().where(rituals.c.client_id == client_id_int)
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_ritual(r) for r in rows]

    def list_all(
        self,
        user_notion_id: str = "",
        result_filter: Optional[str] = None,
    ) -> List[Ritual]:
        """All rituals; optional filter by outcome code, sorted date DESC."""
        stmt = _select_rituals()
        if result_filter:
            code = _code_for(_RESULT_TO_CODE, result_filter) or result_filter
            oc = outcome_status.alias("oc")
            stmt = stmt.where(
                select(oc.c.code)
                .where(oc.c.id == rituals.c.outcome_id)
                .scalar_subquery() == code
            )
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_ritual(r) for r in rows]

    def delete(self, ritual_id: str) -> bool:
        """Hard-delete a ritual row (used in tests / sandbox only)."""
        with get_engine().begin() as conn:
            result = conn.execute(
                rituals.delete().where(rituals.c.id == int(ritual_id))
            )
        return result.rowcount > 0
