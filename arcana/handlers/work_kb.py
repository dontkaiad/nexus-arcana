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


def deadline_keyboard(page_id: str) -> InlineKeyboardMarkup:
    sid = _short(page_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сегодня",  callback_data=f"work_dl:{sid}:today"),
            InlineKeyboardButton(text="Завтра",   callback_data=f"work_dl:{sid}:tomorrow"),
        ],
        [
            InlineKeyboardButton(text="На неделе", callback_data=f"work_dl:{sid}:week"),
            InlineKeyboardButton(text="Без срока", callback_data=f"work_dl:{sid}:none"),
        ],
    ])


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


def _compute_deadline(choice: str, tz_offset: int) -> Optional[str]:
    """choice: today|tomorrow|week|none → ISO 'YYYY-MM-DDTHH:MM' (23:59 локально),
    или None если 'none'."""
    if choice == "none":
        return None
    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    if choice == "today":
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
    elif choice == "tomorrow":
        target = (now + timedelta(days=1)).replace(
            hour=23, minute=59, second=0, microsecond=0,
        )
    elif choice == "week":
        # До конца текущей недели (воскресенье 23:59 по локали).
        days_to_sunday = (6 - now.weekday()) % 7
        if days_to_sunday == 0:
            days_to_sunday = 7  # если уже воскресенье — следующее
        target = (now + timedelta(days=days_to_sunday)).replace(
            hour=23, minute=59, second=0, microsecond=0,
        )
    else:
        return None
    return target.strftime("%Y-%m-%dT%H:%M:%S%z")


@router.callback_query(F.data.startswith("work_dl:"))
async def cb_work_deadline(call: CallbackQuery) -> None:
    await call.answer()
    parts = call.data.split(":", 2)
    if len(parts) != 3:
        return
    _, sid, choice = parts
    from core.user_manager import get_user_notion_id
    from core.shared_handlers import get_user_tz
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    page_id = await _resolve_work_full_id(sid, user_notion_id)
    if not page_id:
        await call.message.answer("⚠️ Работа не найдена.")
        return
    tz_offset = await get_user_tz(call.from_user.id)
    deadline_iso = _compute_deadline(choice, tz_offset)

    from core.notion_client import update_page
    if deadline_iso:
        try:
            await update_page(page_id, {"Дедлайн": {"date": {"start": deadline_iso}}})
        except Exception as e:
            logger.warning("set deadline failed: %s", e)
            await call.message.answer("⚠️ Не получилось выставить дедлайн.")
            return

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    label = {
        "today": "сегодня 23:59",
        "tomorrow": "завтра 23:59",
        "week": "до конца недели",
        "none": "без срока",
    }.get(choice, choice)
    await call.message.answer(f"📅 Дедлайн: {label}")
    if choice != "none":
        await call.message.answer(
            "⏰ Напомнить?",
            reply_markup=reminder_keyboard(page_id),
        )


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
        logger.warning("set reminder failed: %s", e)
        await call.message.answer("⚠️ Не получилось выставить напоминание.")
        return

    label = "за день" if choice == "24h" else "за 3 часа"
    await call.message.answer(f"⏰ Напомню {label} до дедлайна")
