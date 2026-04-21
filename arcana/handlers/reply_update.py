"""arcana/handlers/reply_update.py — reply на сообщение бота = дополнение записи."""
from __future__ import annotations

import logging
import traceback as tb

from aiogram.types import Message

from core.message_pages import get_message_page
from core.reply_update import (
    apply_updates,
    format_applied,
    get_db_id_for_type,
    parse_reply,
)
from core.utils import react

logger = logging.getLogger("arcana.reply_update")


async def handle_reply_update(message: Message) -> bool:
    """Если reply на сообщение бота — попытаться обновить Notion-запись.

    Возвращает True если reply был обработан (отвечаем пользователю),
    False — если не наш reply (пусть идёт в общий route_message).
    """
    orig = message.reply_to_message
    if not orig:
        return False

    mapping = await get_message_page(message.chat.id, orig.message_id)
    if not mapping or mapping.get("bot") != "arcana":
        return False

    page_type = mapping["page_type"]
    page_id = mapping["page_id"]
    reply_text = (message.text or message.caption or "").strip()
    if not reply_text:
        return False

    try:
        updates = await parse_reply(page_type, reply_text)
        if not updates:
            await message.answer("✏️ Не поняла что дополнить.")
            await react(message, "🤔")
            return True

        db_id = get_db_id_for_type(page_type)
        applied = await apply_updates(page_id, page_type, db_id, updates)
        summary = await format_applied(applied)

        await message.answer(f"✏️ Дополнено:\n{summary}")
        await react(message, "✍️")
        return True

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_reply_update error: %s", trace)
        await message.answer("❌ Не удалось дополнить запись.")
        await react(message, "🤡")
        return True
