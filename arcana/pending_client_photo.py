"""arcana/pending_client_photo.py — pending state для /client_photo и reply-flow.

state shape:
  {
    "step": "await_name" | "await_photo" | "await_confirm",
    "client_id": "<notion_page_id>",      # для await_photo / await_confirm
    "client_name": "Маша",
    "file_id": "<telegram file_id>",       # для await_confirm
  }
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import aiosqlite

logger = logging.getLogger("arcana.pending_client_photo")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "pending_client_photo.db")
_TTL = 600  # 10 минут

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS pending_client_photo (
        user_id    INTEGER PRIMARY KEY,
        state      TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
"""


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save(user_id: int, state: Dict[str, Any]) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_client_photo (user_id, state, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, json.dumps(state, ensure_ascii=False), time.time()),
        )
        await db.commit()


async def get(user_id: int) -> Optional[Dict[str, Any]]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT state, created_at FROM pending_client_photo WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            if time.time() - row[1] > _TTL:
                await delete(user_id)
                return None
            return json.loads(row[0])


async def delete(user_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_client_photo WHERE user_id = ?", (user_id,))
        await db.commit()
