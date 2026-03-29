"""arcana/pending_clients.py — pending state для флоу создания клиента. SQLite, TTL 10 мин."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import aiosqlite

logger = logging.getLogger("arcana.pending_clients")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "pending_clients.db")
_TTL = 600  # 10 минут

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS pending_clients (
        user_id    INTEGER PRIMARY KEY,
        state      TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
"""


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save_pending_client(user_id: int, state: Dict[str, Any]) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_clients (user_id, state, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(state, ensure_ascii=False), time.time()),
        )
        await db.commit()


async def get_pending_client(user_id: int) -> Optional[Dict[str, Any]]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT state, created_at FROM pending_clients WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            if time.time() - row[1] > _TTL:
                await delete_pending_client(user_id)
                return None
            return json.loads(row[0])


async def delete_pending_client(user_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_clients WHERE user_id = ?", (user_id,))
        await db.commit()


async def update_pending_client(user_id: int, updates: Dict[str, Any]) -> None:
    state = await get_pending_client(user_id)
    if state is not None:
        # Контакты накапливаются (append уникальных), не перезаписываются
        if "contacts" in updates and "contacts" in state:
            existing = state["contacts"]
            seen = {c["value"] for c in existing}
            for c in updates.pop("contacts"):
                if c.get("value") and c["value"] not in seen:
                    existing.append(c)
                    seen.add(c["value"])
        state.update(updates)
        await save_pending_client(user_id, state)
