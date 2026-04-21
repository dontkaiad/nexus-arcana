"""core/message_pages.py — маппинг msg_id → Notion page_id для reply-флоу."""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import aiosqlite

logger = logging.getLogger("core.message_pages")

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "message_pages.db",
)

_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS message_pages (
        chat_id    INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        page_id    TEXT    NOT NULL,
        page_type  TEXT    NOT NULL,
        bot        TEXT    NOT NULL,
        created_at REAL    NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    )
"""

_TTL = 30 * 24 * 3600  # 30 дней


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_TABLE_DDL)
        await db.commit()


async def save_message_page(
    chat_id: int,
    message_id: int,
    page_id: str,
    page_type: str,
    bot: str,
) -> None:
    """Сохранить маппинг msg_id → Notion page_id."""
    if not page_id or not message_id:
        return
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO message_pages "
            "(chat_id, message_id, page_id, page_type, bot, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, message_id, page_id, page_type, bot, time.time()),
        )
        await db.commit()
    logger.info(
        "save_message_page: chat=%s msg=%s page=%s type=%s bot=%s",
        chat_id, message_id, page_id[:8], page_type, bot,
    )


async def get_message_page(chat_id: int, message_id: int) -> Optional[dict]:
    """Найти маппинг по (chat_id, message_id). Возвращает dict или None."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT page_id, page_type, bot, created_at "
            "FROM message_pages WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    page_id, page_type, bot, created_at = row
    if time.time() - created_at > _TTL:
        # Протухло — убираем
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM message_pages WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            )
            await db.commit()
        return None
    return {
        "page_id": page_id,
        "page_type": page_type,
        "bot": bot,
        "created_at": created_at,
    }


async def cleanup_expired() -> int:
    """Удалить просроченные маппинги. Возвращает число удалённых."""
    await _ensure_table()
    cutoff = time.time() - _TTL
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "DELETE FROM message_pages WHERE created_at < ?", (cutoff,)
        ) as cursor:
            deleted = cursor.rowcount or 0
        await db.commit()
    if deleted:
        logger.info("cleanup_expired: removed %d", deleted)
    return deleted
