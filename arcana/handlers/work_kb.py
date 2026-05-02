"""arcana/handlers/work_kb.py — inline-кнопки дедлайна и напоминания
после создания записи в 🔮 Работы.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

logger = logging.getLogger("arcana.work_kb")

router = Router()


def _short(page_id: str) -> str:
    return page_id.replace("-", "")[:32]


def reminder_keyboard(page_id: str) -> InlineKeyboardMarkup:
    sid = _short(page_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="За день",     callback_data=f"work_rm:{sid}:24h"),
        InlineKeyboardButton(text="За 3 часа",   callback_data=f"work_rm:{sid}:3h"),
        InlineKeyboardButton(text="Без напом.",  callback_data=f"work_rm:{sid}:none"),
    ]])


async def _resolve_work_full_id(short_id: str, user_notion_id: str) -> Optional[str]:
    from core.config import config
    from core.notion_client import query_pages, _with_user_filter
    db_id = config.arcana.db_works
    if not db_id:
        return None
    try:
        pages = await query_pages(
            db_id, filters=_with_user_filter(None, user_notion_id), page_size=200,
        )
    except Exception:
        return None
    for p in pages:
        pid = p.get("id", "").replace("-", "")
        if pid.startswith(short_id) or short_id.startswith(pid[:32]):
            return p.get("id", "")
    return None




@router.callback_query(F.data.startswith("work_rm:"))
async def cb_work_remind(call: CallbackQuery) -> None:
    await call.answer()
    parts = call.data.split(":", 2)
    if len(parts) != 3:
        return
    _, sid, choice = parts
    from core.user_manager import get_user_notion_id
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    page_id = await _resolve_work_full_id(sid, user_notion_id)
    if not page_id:
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if choice == "none":
        await call.message.answer("✅ Без напоминания")
        return

    # Считаем reminder_iso = deadline - offset.
    from core.notion_client import get_page, update_page
    try:
        page = await get_page(page_id)
    except Exception:
        page = None
    deadline = ((page or {}).get("properties", {})
                .get("Дедлайн", {}).get("date") or {}).get("start", "")
    if not deadline:
        await call.message.answer("⚠️ У работы нет дедлайна — напоминание не выставлено.")
        return

    # Парсим ISO; если без зоны — считаем как локальное время в UTC.
    try:
        if "T" in deadline:
            base = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        else:
            base = datetime.fromisoformat(deadline + "T23:59:00+00:00")
    except ValueError:
        await call.message.answer("⚠️ Не смогла распарсить дату дедлайна.")
        return

    offset = {"24h": timedelta(hours=24), "3h": timedelta(hours=3)}.get(choice)
    if not offset:
        return
    reminder_at = base - offset
    iso = reminder_at.isoformat()

    try:
        # Поле «Напоминание» (date) уже использовано в _works_schedule.
        await update_page(page_id, {"Напоминание": {"date": {"start": iso}}})
    except Exception as e:
        logger.warning("set reminder field failed: %s", e)
        await call.message.answer("⚠️ Не получилось выставить напоминание.")
        return

    # Ставим APScheduler-job через shared ReminderScheduler.
    title_parts = ((page or {}).get("properties", {})
                   .get("Работа", {}).get("title") or [])
    title = title_parts[0].get("plain_text", "Работа") if title_parts else "Работа"
    from core.shared_handlers import get_user_tz
    tz_offset = await get_user_tz(call.from_user.id)
    try:
        from arcana.bot import arcana_reminder_flow
        scheduled = await arcana_reminder_flow.schedule_reminder(
            chat_id=call.message.chat.id,
            title=title,
            reminder_dt=iso,
            page_id=page_id,
            tz_offset=int(tz_offset),
        )
    except Exception as e:
        logger.warning("schedule_reminder call failed: %s", e)
        scheduled = False

    label = "за день" if choice == "24h" else "за 3 часа"
    if scheduled:
        await call.message.answer(f"⏰ Напомню {label} до дедлайна")
    else:
        # Поле в Notion записано — на restart restore_reminders подхватит.
        await call.message.answer(
            f"⏰ Напоминание {label} записано в Notion (планировщик догонит при перезапуске)"
        )
