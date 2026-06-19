"""arcana/repos/pg_rituals_repo.py — PostgreSQL adapter for 🕯️ Ритуалы.

Uses SQLAlchemy Core (synchronous psycopg2) wrapped in asyncio.to_thread so
public methods are async and drop-in compatible with the Notion-backed RitualsRepo.
Callers receive plain Ritual dataclasses.
"""
from __future__ import annotations

import asyncio
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

# ── Code → display label maps (for miniapp serializers) ──────────────────────

CODE_TO_GOAL = {
    "attract": "🧲 Привлечение",
    "protect": "🛡️ Защита",
    "cleanse": "🌊 Очищение",
    "love": "💕 Любовь",
    "finance": "💰 Финансы",
    "destruct_return": "💀 Деструктив",
    "cut_off": "⚔️ Развязка",
    "love_bind": "💘 Приворот",
    "other": "🔮 Другое",
}

CODE_TO_PLACE = {
    "home": "🏠 Дома",
    "forest": "🌲 Лес",
    "graveyard": "✝️ Погост",
    "crossroad": "🛤️ Перекрёсток",
    "church": "⛪ Церковь",
    "water": "🌊 Водоём",
    "field": "🌾 Поле",
    "other": "📍 Другое",
}

CODE_TO_RESULT = {
    "unverified": "⏳ Не проверено",
    "partial": "〰️ Частично",
    "negative": "❌ Не сработало",
    "positive": "✅ Сработало",
}

CODE_TO_TYPE = {
    "personal": "👤 Личный",
    "client": "🤝 Клиентский",
}

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
    "⏳ не проверено":  "unverified",
    "〰️ частично":     "partial",
    "❌ не сработал":   "negative",
    "✅ сработал":      "positive",
    # Notion label forms (verb agreement variant)
    "❌ не сработало":  "negative",
    "✅ сработало":     "positive",
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
        type_code=getattr(row, "type_code", None),
        time_min=int(row.duration_min) if getattr(row, "duration_min", None) else None,
        consumables=getattr(row, "consumables", None) or "",
        offerings=getattr(row, "offerings", None) or "",
        powers=getattr(row, "forces", None) or "",
        structure=getattr(row, "structure", None) or "",
        notes=getattr(row, "notes", None) or None,
        photo_url=getattr(row, "photo_url", None) or None,
    )


def _client_id_int(client_id: Optional[str]) -> Optional[int]:
    """Convert caller-supplied PG int str to int. None if absent or invalid."""
    if not client_id:
        return None
    try:
        return int(client_id)
    except (ValueError, TypeError):
        logger.warning("rituals: invalid client_id %r", client_id)
        return None


# ── Base SELECT with all joins (DRY) ─────────────────────────────────────────

def _select_rituals():
    """Base query: rituals + joined lookup codes + all detail columns."""
    oc = outcome_status.alias("oc")
    mp = magical_purpose.alias("mp")
    rp = ritual_place.alias("rp")
    et = engagement_type.alias("et")
    return (
        select(
            rituals.c.id,
            rituals.c.title,
            rituals.c.occurred_at,
            rituals.c.client_id,
            rituals.c.price,
            rituals.c.paid,
            rituals.c.photo_url,
            rituals.c.forces,
            rituals.c.structure,
            rituals.c.consumables,
            rituals.c.offerings,
            rituals.c.notes,
            rituals.c.duration_min,
            oc.c.code.label("outcome_code"),
            mp.c.code.label("purpose_code"),
            rp.c.code.label("place_code"),
            et.c.code.label("type_code"),
        )
        .outerjoin(oc,  rituals.c.outcome_id  == oc.c.id)
        .outerjoin(mp,  rituals.c.purpose_id  == mp.c.id)
        .outerjoin(rp,  rituals.c.place_id    == rp.c.id)
        .outerjoin(et,  rituals.c.type_id     == et.c.id)
        .order_by(rituals.c.occurred_at.desc().nullslast())
    )


# ── Public adapter ────────────────────────────────────────────────────────────

class PgRitualsRepo:
    """PostgreSQL adapter for the rituals domain.

    Drop-in replacement for the Notion-backed RitualsRepo once cutover happens.
    Public methods are async (via asyncio.to_thread) so callers can await them
    identically to the Notion adapter. Blocking I/O stays in a thread pool.
    """

    # ── Private sync implementations ─────────────────────────────────────────

    def _create_sync(
        self,
        name: str,
        date: str,
        ritual_type: str,
        consumables: str,
        consumables_cost: float,
        duration_min: float,
        offerings: str,
        forces: str,
        structure: str,
        amount: float,
        paid: float,
        client_id: Optional[str],
        user_notion_id: str,
        goal: Optional[str],
        place: Optional[str],
        notes: Optional[str],
        payment_source: Optional[str],
        offerings_cost: Optional[float],
    ) -> Optional[Ritual]:
        type_code   = _code_for(_TYPE_TO_CODE,    ritual_type)
        goal_code   = _code_for(_GOAL_TO_CODE,    goal)
        place_code  = _code_for(_PLACE_TO_CODE,   place)
        pay_code    = _code_for(_PAYMENT_TO_CODE, payment_source)
        result_code = "unverified"

        occurred_at: Optional[datetime] = None
        if date:
            try:
                occurred_at = datetime.strptime(date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning("create: bad date %r — storing NULL", date)

        with get_engine().begin() as conn:
            type_id    = _resolve(conn, engagement_type,  type_code)
            purpose_id = _resolve(conn, magical_purpose,  goal_code)
            place_id   = _resolve(conn, ritual_place,     place_code)
            pay_id     = _resolve(conn, t_payment_source, pay_code)
            outcome_id = _resolve(conn, outcome_status,   result_code)

            client_id_int = _client_id_int(client_id)

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

    def _list_by_client_sync(
        self,
        client_id: str,
        user_notion_id: str,
    ) -> List[Ritual]:
        cid_int = _client_id_int(client_id)
        if cid_int is None:
            return []
        stmt = (
            _select_rituals()
            .where(rituals.c.client_id == cid_int)
            .where(rituals.c.archived == False)  # noqa: E712
        )
        with get_engine().connect() as conn:
            rows = conn.execute(stmt).fetchall()
        return [_row_to_ritual(r) for r in rows]

    def _list_all_sync(
        self,
        user_notion_id: str,
        result_filter: Optional[str],
    ) -> List[Ritual]:
        stmt = _select_rituals().where(rituals.c.archived == False)  # noqa: E712
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

    def _delete_sync(self, ritual_id: str) -> bool:
        with get_engine().begin() as conn:
            result = conn.execute(
                rituals.delete().where(rituals.c.id == int(ritual_id))
            )
        return result.rowcount > 0

    def _archive_sync(self, ritual_id: str) -> bool:
        try:
            rid = int(ritual_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            res = conn.execute(
                rituals.update().where(rituals.c.id == rid).values(archived=True)
            )
        return res.rowcount > 0

    def _set_result_sync(self, ritual_id: str, result_code: str) -> bool:
        code = _code_for(_RESULT_TO_CODE, result_code) or result_code
        with get_engine().begin() as conn:
            outcome_id = _resolve(conn, outcome_status, code)
            if outcome_id is None:
                logger.warning("set_result: unknown result_code %r (resolved=%r)", result_code, code)
                return False
            res = conn.execute(
                rituals.update()
                .where(rituals.c.id == int(ritual_id))
                .values(outcome_id=outcome_id)
            )
        return res.rowcount > 0

    # ── Public async interface (drop-in for Notion adapter) ───────────────────

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
        return await asyncio.to_thread(
            self._create_sync,
            name, date, ritual_type, consumables, consumables_cost,
            duration_min, offerings, forces, structure, amount, paid,
            client_id, user_notion_id, goal, place, notes,
            payment_source, offerings_cost,
        )

    async def list_by_client(
        self,
        client_id: str,
        user_notion_id: str = "",
    ) -> List[Ritual]:
        return await asyncio.to_thread(
            self._list_by_client_sync, client_id, user_notion_id
        )

    async def list_all(
        self,
        user_notion_id: str = "",
        result_filter: Optional[str] = None,
    ) -> List[Ritual]:
        return await asyncio.to_thread(
            self._list_all_sync, user_notion_id, result_filter
        )

    async def delete(self, ritual_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, ritual_id)

    async def archive(self, ritual_id: str) -> bool:
        """Soft-delete: помечает archived=True (запись остаётся в БД,
        пропадает из list_all/list_by_client, но находится find_by_id)."""
        return await asyncio.to_thread(self._archive_sync, ritual_id)

    async def set_result(self, ritual_id: str, result_code: str) -> bool:
        return await asyncio.to_thread(self._set_result_sync, ritual_id, result_code)

    def _find_by_id_sync(self, ritual_id: str) -> Optional[Ritual]:
        try:
            rid = int(ritual_id)
        except (ValueError, TypeError):
            return None
        stmt = _select_rituals().where(rituals.c.id == rid)
        with get_engine().connect() as conn:
            row = conn.execute(stmt).fetchone()
        return _row_to_ritual(row) if row else None

    def _update_photo_url_sync(self, ritual_id: str, url: str) -> bool:
        try:
            rid = int(ritual_id)
        except (ValueError, TypeError):
            return False
        with get_engine().begin() as conn:
            res = conn.execute(
                rituals.update().where(rituals.c.id == rid).values(photo_url=url)
            )
        return res.rowcount > 0

    async def find_by_id(self, ritual_id: str) -> Optional[Ritual]:
        return await asyncio.to_thread(self._find_by_id_sync, ritual_id)

    async def update_photo_url(self, ritual_id: str, url: str) -> bool:
        return await asyncio.to_thread(self._update_photo_url_sync, ritual_id, url)
