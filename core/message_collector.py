"""
core/message_collector.py — Message collector с debounce.

Собирает все сообщения от юзера за окно тишины, потом отдаёт батч на обработку.

Принцип:
1. Первое сообщение → сохраняется в буфер (SQLite)
2. Каждое следующее → добавляется в буфер, таймер сбрасывается
3. После DEBOUNCE_SEC секунд тишины → вызывается callback с uid
4. Callback получает список через get_buffer():
   [{"type": "text"|"photo"|"voice"|"contact", "content": "...", "caption": "...", "ts": float}]
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Dict, List, Any, Optional

import aiosqlite

logger = logging.getLogger("core.message_collector")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "message_buffer.db")
DEBOUNCE_SEC = 5  # секунд тишины перед обработкой

_timers: Dict[int, asyncio.Task] = {}  # uid → debounce task
_batch_callback: Optional[Callable[[int], Awaitable[None]]] = None


def register_batch_callback(cb: Callable[[int], Awaitable[None]]) -> None:
    """Зарегистрировать callback для обработки батча (вызывать из bot.main())."""
    global _batch_callback
    _batch_callback = cb


def get_registered_callback() -> Optional[Callable[[int], Awaitable[None]]]:
    """Получить зарегистрированный callback."""
    return _batch_callback


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                msg_type TEXT NOT NULL,
                content TEXT NOT NULL,
                caption TEXT,
                created_at REAL NOT NULL
            )
        """)
        await db.commit()


async def add_message(
    user_id: int,
    msg_type: str,   # "text", "photo", "voice", "contact"
    content: str,    # текст / base64 фото / расшифровка / форматированный контакт
    caption: str = "",
) -> None:
    """Добавить сообщение в буфер."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO message_buffer (user_id, msg_type, content, caption, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, msg_type, content, caption, time.time()),
            )
            await db.commit()
    except Exception as e:
        logger.error("add_message uid=%s type=%s: %s", user_id, msg_type, e)


async def get_buffer(user_id: int) -> List[Dict[str, Any]]:
    """Получить все сообщения из буфера, отсортированные по времени."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT msg_type, content, caption, created_at FROM message_buffer WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [{"type": r[0], "content": r[1], "caption": r[2] or "", "ts": r[3]} for r in rows]
    except Exception as e:
        logger.error("get_buffer uid=%s: %s", user_id, e)
        return []


async def clear_buffer(user_id: int) -> None:
    """Очистить буфер юзера."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM message_buffer WHERE user_id = ?", (user_id,))
            await db.commit()
    except Exception as e:
        logger.error("clear_buffer uid=%s: %s", user_id, e)


async def has_buffer(user_id: int) -> bool:
    """Есть ли сообщения в буфере."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM message_buffer WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return (row[0] or 0) > 0
    except Exception as e:
        logger.error("has_buffer uid=%s: %s", user_id, e)
        return False


def schedule_processing(
    user_id: int,
    callback: Callable[[int], Awaitable[None]],
    debounce: float = DEBOUNCE_SEC,
) -> None:
    """Запланировать обработку буфера через debounce секунд.
    Каждый вызов сбрасывает таймер — реализует debounce."""

    # Отменить предыдущий таймер
    old = _timers.pop(user_id, None)
    if old and not old.done():
        old.cancel()

    async def _wait_and_process() -> None:
        await asyncio.sleep(debounce)
        _timers.pop(user_id, None)
        try:
            await callback(user_id)
        except Exception as e:
            logger.error("collector callback error uid=%s: %s", user_id, e)

    _timers[user_id] = asyncio.create_task(_wait_and_process())


def cancel_timer(user_id: int) -> None:
    """Отменить debounce таймер без запуска callback."""
    old = _timers.pop(user_id, None)
    if old and not old.done():
        old.cancel()
