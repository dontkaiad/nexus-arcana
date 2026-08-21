"""nexus/handlers/reply_update.py — reply на сообщение бота = дополнение записи."""
from __future__ import annotations

import logging
import re
import traceback as tb
from datetime import datetime, timezone

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

_MEMORY_PLAQUE_RE = re.compile(r"^🧠 Запомнил(?:\s*\[[^\]]*\])?:\s*(.+)$", re.DOTALL)


async def _move_memory_to_notes(message: Message, orig: Message, page_id: str, user_notion_id: str) -> bool:
    """Перенос факта из 🧠 Память в 📝 Заметки: архивирует memory-строку +
    создаёт note с тем же текстом (#188). Текст факта берём из ТЕКСТА плашки
    (не из БД) — плашка уже содержит канонический fact, лишний round-trip
    в PgMemoryRepo не нужен. Возвращает True если обработано (плашка
    распозналась), False — пусть падает в обычный field-update путь."""
    orig_text = orig.text or orig.caption or ""
    m = _MEMORY_PLAQUE_RE.match(orig_text)
    if not m:
        return False
    fact_text = m.group(1).strip()
    if not fact_text:
        return False
    try:
        from core.repos.memory_repo import _repo as mem_repo
        from nexus.repos.notes_repo import NotesRepo
        today = datetime.now(timezone.utc).date().isoformat()
        note_id = await NotesRepo().add(text=fact_text, tags=[], date=today, user_notion_id=user_notion_id)
        if not note_id:
            await message.answer("⚠️ Не получилось создать заметку.")
            return True
        await mem_repo.archive(page_id)
        await message.answer(f"📝 Перенесено в заметки: {fact_text}")
        await react(message, "✍️")
    except Exception as e:
        logger.error("_move_memory_to_notes: %s", e)
        await message.answer("❌ Не удалось перенести в заметки.")
    return True


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

        # #188: «это в заметки» на плашку памяти — не field-апдейт, а перенос
        # в другой домен (архив факта + новая запись в 📝 Заметки). pop() ДО
        # проверки "нечего менять", т.к. move_to_notes=False всегда присутствует
        # ключом в JSON-ответе Haiku (не фильтруется как null/"").
        if page_type == "memory" and updates.pop("move_to_notes", False):
            handled = await _move_memory_to_notes(message, orig, page_id, user_notion_id)
            if handled:
                return True

        if not updates:
            await message.answer("✏️ Не поняла что дополнить.")
            await react(message, "🤔")
            return True

        db_id = get_db_id_for_type(page_type)
        applied = await apply_updates(
            page_id, page_type, db_id, updates,
            user_notion_id=user_notion_id, tz_offset=tz_offset,
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
