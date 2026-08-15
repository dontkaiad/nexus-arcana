"""arcana/pending_grimoire_search.py — pending state для «🔍 Поиск» в Гримуаре. SQLite, НЕ in-memory.

#105: раньше _pending_search в grimoire.py был обычным dict — рестарт бота
между кликом «Поиск» и вводом запроса терял состояние (сообщение либо не
маршрутизировалось в поиск, либо трактовалось как что-то другое).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import aiosqlite

logger = logging.getLogger("arcana.pending_grimoire_search")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "pending_grimoire_search.db")

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS pending_grimoire_search (
        user_id         INTEGER PRIMARY KEY,
        user_notion_id  TEXT    NOT NULL,
        created_at      REAL    NOT NULL
    )
"""

_TTL = 600  # 10 минут


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save_pending_search(user_id: int, user_notion_id: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_grimoire_search "
            "(user_id, user_notion_id, created_at) VALUES (?, ?, ?)",
            (user_id, user_notion_id, time.time()),
        )
        await db.commit()


async def pop_pending_search(user_id: int) -> Optional[str]:
    """Вернуть user_notion_id и удалить запись — если не найдена/протухла, None."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_notion_id, created_at FROM pending_grimoire_search WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        await db.execute(
            "DELETE FROM pending_grimoire_search WHERE user_id = ?", (user_id,)
        )
        await db.commit()
    if not row:
        return None
    user_notion_id, created_at = row
    if time.time() - created_at > _TTL:
        return None
    return user_notion_id
