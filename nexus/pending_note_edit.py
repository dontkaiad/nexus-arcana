"""nexus/pending_note_edit.py — pending state для "заменить тег заметки". SQLite, НЕ in-memory.

#114: раньше это состояние жило в общем in-memory _pending словаре notes.py —
рестарт бота между кликом на кнопку тега и выбором замены терял состояние,
и клик по кнопке отвечал «сессия истекла» без возможности восстановиться.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger("nexus.pending_note_edit")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "pending_note_edit.db")

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS pending_note_edit (
        user_id    INTEGER PRIMARY KEY,
        state      TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
"""

_TTL = 600  # 10 минут


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save_pending_note_edit(user_id: int, page_id: str, current_tags: List[str], new_value: str) -> None:
    await _ensure_table()
    state = {"page_id": page_id, "current_tags": current_tags, "new_value": new_value}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_note_edit (user_id, state, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(state, ensure_ascii=False), time.time()),
        )
        await db.commit()


async def pop_pending_note_edit(user_id: int) -> Optional[Dict[str, Any]]:
    """Вернуть {page_id, current_tags, new_value} и удалить — None если нет/протухло."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT state, created_at FROM pending_note_edit WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        await db.execute(
            "DELETE FROM pending_note_edit WHERE user_id = ?", (user_id,)
        )
        await db.commit()
    if not row:
        return None
    state_raw, created_at = row
    if time.time() - created_at > _TTL:
        return None
    return json.loads(state_raw)
