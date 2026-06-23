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


async def handle_reply_update(message: Message, user_notion_id: str = "") -> bool:
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

    # Reply на карточку триплета = правка свободным текстом (карта/трактовка),
    # как кнопка «Поправить» и как reply-правка в Nexus. Раньше session-reply шёл
    # в ограниченный _apply_session (только тема/область) → смена карты не
    # работала, а «королева мечей не король жезлов» свободным текстом улетала в
    # НОВЫЙ расклад. Теперь reply → полный триплет-correction (#B8 #B9).
    if page_type == "session":
        from arcana.handlers.sessions import correct_triplet_by_id
        try:
            ok = await correct_triplet_by_id(message, reply_text, page_id, user_notion_id)
        except Exception as e:
            logger.error("session reply correction failed: %s", tb.format_exc())
            ok = False
        if not ok:
            await message.answer("⚠️ Триплет не найден для правки.")
        await react(message, "✍️")
        return True

    try:
        updates = await parse_reply(page_type, reply_text)
        if not updates:
            await message.answer("✏️ Не поняла что дополнить.")
            await react(message, "🤔")
            return True

        db_id = get_db_id_for_type(page_type)
        applied = await apply_updates(
            page_id, page_type, db_id, updates, user_notion_id=user_notion_id
        )
        summary = await format_applied(applied)

        # Спец-кейс: reply на работу выставил дедлайн → авто-напоминание
        # (deadline - 1 день), как в новом preview-flow.
        if page_type == "work" and "Дедлайн" in applied:
            try:
                import asyncio as _asyncio
                from datetime import datetime, timedelta
                from core.shared_handlers import get_user_tz
                from arcana.bot import arcana_reminder_flow
                from arcana.repos.works_tables import works as t_works
                from core.db import get_engine

                deadline = applied.get("Дедлайн") or ""
                iso = deadline if "T" in deadline else f"{deadline[:10]}T09:00"
                dt = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
                reminder = (dt - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
                tz_offset = await get_user_tz(message.from_user.id)
                await arcana_reminder_flow.schedule_reminder(
                    chat_id=message.chat.id,
                    title=applied.get("Работа") or "Работа",
                    reminder_dt=reminder,
                    page_id=page_id,
                    tz_offset=int(tz_offset),
                )

                def _set_reminder():
                    rd = datetime.strptime(reminder, "%Y-%m-%dT%H:%M")
                    with get_engine().begin() as conn:
                        conn.execute(
                            t_works.update()
                            .where(t_works.c.id == int(page_id))
                            .values(reminder=rd)
                        )
                await _asyncio.to_thread(_set_reminder)
                summary += f"\n🔔 Напомню: {reminder.replace('T', ' ')}"
            except Exception as e:
                logger.warning("auto-reminder on reply failed: %s", e)

        await message.answer(f"✏️ Дополнено:\n{summary}")
        await react(message, "✍️")
        return True

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_reply_update error: %s", trace)
        await message.answer("❌ Не удалось дополнить запись.")
        await react(message, "🤡")
        return True
