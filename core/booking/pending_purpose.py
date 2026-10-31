"""core/booking/pending_purpose.py — незавершённая бронь друга в Postgres.

Между выбором слота и текстом «на что» Заря хранит {ctx, ep, hours, role,
chat_id, ts} по tg_id. Раньше — in-memory dict (терялся при рестарте бота).
TTL проверяется здесь: просроченная запись удаляется и читается как None.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa

PURPOSE_TTL_SEC = 15 * 60

_table = sa.Table(
    "zarya_pending_purpose", sa.MetaData(),
    sa.Column("tg_id", sa.BigInteger, primary_key=True),
    sa.Column("payload", sa.Text, nullable=False),
)


def _engine():
    from core.db import get_engine
    return get_engine()


def _set_sync(tg_id: int, payload: str) -> None:
    with _engine().begin() as conn:
        conn.execute(_table.delete().where(_table.c.tg_id == tg_id))
        conn.execute(_table.insert().values(tg_id=tg_id, payload=payload))


def _get_sync(tg_id: int) -> Optional[str]:
    with _engine().connect() as conn:
        row = conn.execute(sa.select(_table.c.payload).where(_table.c.tg_id == tg_id)).first()
    return row[0] if row else None


def _pop_sync(tg_id: int) -> Optional[str]:
    with _engine().begin() as conn:
        row = conn.execute(
            _table.delete().where(_table.c.tg_id == tg_id).returning(_table.c.payload)
        ).first()
    return row[0] if row else None


def _decode(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    ts = data.get("ts")
    if ts is not None and datetime.now(timezone.utc).timestamp() - ts > PURPOSE_TTL_SEC:
        return None
    return data


async def set_pending(tg_id: int, data: dict) -> None:
    await asyncio.to_thread(_set_sync, tg_id, json.dumps(data))


async def get_pending(tg_id: int) -> Optional[dict]:
    """Live pending dict or None; an expired row is deleted on read."""
    raw = await asyncio.to_thread(_get_sync, tg_id)
    data = _decode(raw)
    if raw and data is None:
        await asyncio.to_thread(_pop_sync, tg_id)
    return data


async def pop_pending(tg_id: int) -> Optional[dict]:
    return _decode(await asyncio.to_thread(_pop_sync, tg_id))
