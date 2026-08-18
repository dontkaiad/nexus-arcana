"""nexus/handlers/reply_update.py — reply на сообщение бота = дополнение записи."""
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

logger = logging.getLogger("nexus.reply_update")


async def handle_reply_update(message: Message, user_notion_id: str = "") -> bool:
    """Если reply на сообщение бота — попытаться обновить Notion-запись.

    Возвращает True если обработано (сообщение уже отправлено пользователю),
    False — если не наш reply.
    """
    orig = message.reply_to_message
    if not orig:
        return False

    mapping = await get_message_page(message.chat.id, orig.message_id)
    if not mapping or mapping.get("bot") != "nexus":
        return False

    page_type = mapping["page_type"]
    page_id = mapping["page_id"]
    reply_text = (message.text or message.caption or "").strip()
    if not reply_text:
        return False

    try:
        tz_offset = 3
        if page_type == "task":
            from nexus.handlers.tasks import _get_user_tz
            tz_offset = await _get_user_tz(message.from_user.id)
        updates = await parse_reply(page_type, reply_text, tz_offset=tz_offset)
        if not updates:
            await message.answer("✏️ Не поняла что дополнить.")
            await react(message, "🤔")
            return True

        db_id = get_db_id_for_type(page_type)
        applied = await apply_updates(
            page_id, page_type, db_id, updates, user_notion_id=user_notion_id
        )
        summary = await format_applied(applied)

        # Дедлайн/напоминание в reply — не только колонка в БД, это ещё и
        # APScheduler job (reminder_{id}/deadline_{id}, см. TASKS.md
        # "reminder/deadline — projections"). Без перепланирования правка
        # молча повисает: старый job с прежней датой продолжает жить, а
        # новая дата никогда не сработает до рестарта бота.
        if page_type == "task" and ("Дедлайн" in applied or "Напоминание" in applied):
            try:
                from nexus.repos.tasks_repo import _repo as _tasks_repo
                from nexus.handlers.tasks import _schedule_reminder, _schedule_deadline_check

                task = await _tasks_repo.retrieve_page(page_id)
                title = task.title if task else "Задача"
                if "Напоминание" in applied:
                    await _schedule_reminder(
                        message.chat.id, title, applied["Напоминание"], page_id, tz_offset,
                    )
                if "Дедлайн" in applied:
                    await _schedule_deadline_check(
                        message.chat.id, title, applied["Дедлайн"], page_id, tz_offset,
                    )
            except Exception as e:
                logger.warning("reply_update: live reschedule failed for %s: %s", page_id, e)

        await message.answer(f"✏️ Дополнено:\n{summary}")
        await react(message, "✍️")
        return True

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_reply_update error: %s", trace)
        await message.answer("❌ Не удалось дополнить запись.")
        await react(message, "🤡")
        return True
