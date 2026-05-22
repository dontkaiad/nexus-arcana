"""core/task_reminder_msg.py — какое сообщение в чате является живым
напоминанием задачи.

При отправке плашки «… Сделано?» с кнопками task_complete_{id} сохраняем
её (chat_id, message_id). Когда задачу отмечают где-то ещё (Mini App),
по task_id находим плашку и гасим её (см. core.bot_notify.clear_task_reminder).

TTL 47ч: Telegram не даёт редактировать сообщения старше 48ч — после этого
строка бесполезна.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import aiosqlite

logger = logging.getLogger("core.task_reminder_msg")

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "task_reminder_msg.db",
)

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS task_reminder_msg (
        task_id    TEXT    NOT NULL PRIMARY KEY,
        chat_id    INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        title      TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
"""

# Telegram запрещает editMessageText для сообщений старше 48ч.
_TTL = 47 * 3600


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save_task_reminder(task_id: str, chat_id: int, message_id: int, title: str) -> None:
    """Запомнить живую плашку задачи. Перезаписывает прошлую (одна на task)."""
    if not task_id or not message_id:
        return
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO task_reminder_msg "
            "(task_id, chat_id, message_id, title, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, chat_id, message_id, title or "", time.time()),
        )
        await db.commit()


async def get_task_reminder(task_id: str) -> Optional[dict]:
    """Живая плашка задачи или None (None если строки нет или старше 47ч)."""
    if not task_id:
        return None
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT chat_id, message_id, title, created_at "
            "FROM task_reminder_msg WHERE task_id=?",
            (task_id,),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    chat_id, message_id, title, created_at = row
    if time.time() - created_at > _TTL:
        await delete_task_reminder(task_id)
        return None
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "title": title,
        "created_at": created_at,
    }


async def delete_task_reminder(task_id: str) -> None:
    if not task_id:
        return
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM task_reminder_msg WHERE task_id=?", (task_id,))
        await db.commit()
