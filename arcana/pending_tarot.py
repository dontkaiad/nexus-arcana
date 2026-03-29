"""arcana/pending_tarot.py — pending state для многошагового флоу таро. SQLite, НЕ in-memory."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import aiosqlite

logger = logging.getLogger("arcana.pending_tarot")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "pending_tarot.db")

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS pending_sessions (
        user_id    INTEGER PRIMARY KEY,
        state      TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
"""

_TTL = 3600  # 1 час


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save_pending(user_id: int, state: Dict[str, Any]) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_sessions (user_id, state, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(state, ensure_ascii=False), time.time()),
        )
        await db.commit()


async def get_pending(user_id: int) -> Optional[Dict[str, Any]]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT state, created_at FROM pending_sessions WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            if time.time() - row[1] > _TTL:
                await delete_pending(user_id)
                return None
            return json.loads(row[0])


async def delete_pending(user_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM pending_sessions WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def update_pending(user_id: int, updates: Dict[str, Any]) -> None:
    state = await get_pending(user_id)
    if state is not None:
        state.update(updates)
        await save_pending(user_id, state)
