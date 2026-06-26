"""arcana/handlers/work_reminder_kb.py — callback'и кнопок на напоминаниях Работ.

Кнопки генерирует core/reminder_scheduler.py (prefix=work):
  work_complete_<id>  — выполнено
  work_reschedule_<id> — перенести (запрашивает новое время)
  work_delete_<id>    — архивировать
  work_wip_<id>       — в процессе (статус остаётся open, запрашивает перенос)

Текстовый ввод нового времени перехватывается из base.py:route_message
через has_pending_reschedule / handle_work_reschedule_text.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message

from core.claude_client import ask_claude
from core.layout import maybe_convert

logger = logging.getLogger("arcana.work_reminder_kb")
router = Router()

# ── in-memory pending reschedule ───────────────────────────────────────────────
# uid → {"work_id": str, "title": str, "ts": float}
_pending: dict = {}
_PENDING_TTL = 300  # 5 минут


def _pending_set(uid: int, work_id: str, title: str) -> None:
    _pending[uid] = {"work_id": work_id, "title": title, "ts": time.time()}


def _pending_pop(uid: int) -> Optional[dict]:
    entry = _pending.pop(uid, None)
    if entry and time.time() - entry["ts"] > _PENDING_TTL:
        return None
    return entry


def has_pending_reschedule(uid: int) -> bool:
    entry = _pending.get(uid)
    if not entry:
        return False
    if time.time() - entry["ts"] > _PENDING_TTL:
        _pending.pop(uid, None)
        return False
    return True


# ── helpers ────────────────────────────────────────────────────────────────────

_SUPPORT_WIP = [
    "Огонь, в процессе — напомню! ⚡",
    "Принято, работаем 💪 Когда пнуть снова?",
    "Ок, в процессе. Когда напомнить?",
    "Трекаем! Напомнить через сколько?",
]

_RESCHEDULE_PROMPT = (
    "⏰ <b>Когда напомнить снова?</b>\n"
    "Примеры: <code>завтра в 10:00</code>, <code>через 2 часа</code>, <code>в понедельник</code>"
)


def _get_title(msg_text: str) -> str:
    if "Напоминание:" in msg_text:
        return msg_text.split("Напоминание:")[1].strip().split("\n")[0].strip()
    if "Дедлайн:" in msg_text:
        return msg_text.split("Дедлайн:")[1].strip().split("\n")[0].strip()
    return ""


async def _get_tz(uid: int) -> int:
    from core.shared_handlers import get_user_tz
    return int(await get_user_tz(uid))


def _parse_relative_time(text: str, tz_offset: int) -> Optional[str]:
    import re
    m = re.search(r"через\s+(\d+)\s*(мин\w*|час\w*|ч\b|дн\w*|день|дня|дней)", text, re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    if unit.startswith("мин"):
        result = now + timedelta(minutes=n)
    elif unit.startswith("ч") or unit.startswith("час"):
        result = now + timedelta(hours=n)
    else:
        result = now + timedelta(days=n)
    return result.strftime("%Y-%m-%dT%H:%M")


# ── callbacks ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("work_complete_"))
async def work_complete(call: CallbackQuery) -> None:
    work_id = call.data.split("_", 2)[2]
    logger.info("work_complete: work_id=%s uid=%s", work_id, call.from_user.id)

    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        repo = PgWorksRepo()
        await repo.mark_done(work_id)
    except Exception as e:
        logger.error("work_complete: mark_done failed: %s", e)
        await call.answer("⚠️ Ошибка обновления", show_alert=True)
        return

    try:
        from arcana.bot import arcana_reminder_flow
        arcana_reminder_flow.remove_jobs(work_id)
    except Exception as e:
        logger.warning("work_complete: remove_jobs failed: %s", e)

    title = _get_title(call.message.text or "")
    await call.message.edit_reply_markup()
    await call.answer("✅ Выполнено!")
    title_line = f"\n✅ {title} — выполнено" if title else "\n✅ Выполнено"
    await call.message.reply(f"💅 Готово!{title_line}")


@router.callback_query(F.data.startswith("work_reschedule_"))
async def work_reschedule(call: CallbackQuery) -> None:
    work_id = call.data.split("_", 2)[2]
    uid = call.from_user.id
    logger.info("work_reschedule: work_id=%s uid=%s", work_id, uid)

    title = _get_title(call.message.text or "")
    _pending_set(uid, work_id, title)
    await call.message.edit_reply_markup()
    await call.answer()
    await call.message.answer(
        f"📌 Разумно! Лучше перенести чем забить.\n\n{_RESCHEDULE_PROMPT}",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("work_delete_"))
async def work_delete(call: CallbackQuery) -> None:
    work_id = call.data.split("_", 2)[2]
    logger.info("work_delete: work_id=%s uid=%s", work_id, call.from_user.id)

    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        repo = PgWorksRepo()
        await repo.set_status(work_id, "archived")
    except Exception as e:
        logger.error("work_delete: set_status failed: %s", e)
        await call.answer("⚠️ Ошибка обновления", show_alert=True)
        return

    try:
        from arcana.bot import arcana_reminder_flow
        arcana_reminder_flow.remove_jobs(work_id)
    except Exception as e:
        logger.warning("work_delete: remove_jobs failed: %s", e)

    await call.message.edit_reply_markup()
    await call.answer("🗑 Архивировано")
    await call.message.reply("🗂 Работа перенесена в архив.")


@router.callback_query(F.data.startswith("work_wip_"))
async def work_wip(call: CallbackQuery) -> None:
    """Статус остаётся open (работа в процессе) — запрашиваем новое время напоминания."""
    work_id = call.data.split("_", 2)[2]
    uid = call.from_user.id
    logger.info("work_wip: work_id=%s uid=%s", work_id, uid)

    title = _get_title(call.message.text or "")
    _pending_set(uid, work_id, title)
    await call.message.edit_reply_markup()
    await call.answer("⏳ В процессе")
    support = random.choice(_SUPPORT_WIP)
    await call.message.answer(
        f"{support}\n\n{_RESCHEDULE_PROMPT}",
        parse_mode="HTML",
    )


# ── text handler (called from base.py:route_message) ──────────────────────────

async def handle_work_reschedule_text(message: Message) -> bool:
    """Обработать текст нового времени для переноса Work-напоминания.

    Возвращает True если pending был установлен и обработан (caller должен return).
    """
    uid = message.from_user.id
    entry = _pending.get(uid)
    if not entry or time.time() - entry["ts"] > _PENDING_TTL:
        _pending.pop(uid, None)
        return False

    work_id = entry["work_id"]
    title = entry.get("title") or "Работа"
    tz_offset = await _get_tz(uid)
    text = maybe_convert((message.text or "").strip())

    # Быстрый парсер relative
    relative = _parse_relative_time(text, tz_offset)
    if relative:
        return await _do_reschedule(message, uid, work_id, title, relative, tz_offset)

    now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")
    system = (
        "Пользователь переносит напоминание. Верни ТОЛЬКО JSON без markdown:\n"
        '{\"reminder_time\": \"YYYY-MM-DDTHH:MM\"}\n\n'
        "Правила:\n"
        "- reminder_time строго в будущем.\n"
        "- «в 19» → сегодня в 19:00 (если прошло — завтра).\n"
        "- «завтра в 10:00» → завтра в 10:00.\n"
        "- «в понедельник» → ближайший понедельник в 09:00.\n"
        f"Сейчас: {now_str} (UTC+{tz_offset})"
    )

    try:
        raw = await ask_claude(
            text, system=system, max_tokens=60,
            model="claude-haiku-4-5-20251001", temperature=0,
        )
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        reminder_time = parsed.get("reminder_time")
    except Exception as e:
        logger.error("work reschedule parse error: %s", e)
        await message.answer("❌ Не смогла распарсить время. Попробуй ещё раз.")
        return True

    if not reminder_time:
        await message.answer(
            "⏰ Не поняла когда. Укажи время:\n"
            "<code>завтра в 10:00</code>, <code>в 15:00</code>, <code>через 2 часа</code>",
            parse_mode="HTML",
        )
        return True

    return await _do_reschedule(message, uid, work_id, title, reminder_time, tz_offset)


async def _do_reschedule(
    message: Message, uid: int, work_id: str, title: str, reminder_iso: str, tz_offset: int
) -> bool:
    try:
        from arcana.bot import arcana_reminder_flow
        from arcana.repos.works_tables import works as t_works
        from core.db import get_engine
        import asyncio

        ok = await arcana_reminder_flow.schedule_reminder(
            chat_id=message.chat.id,
            title=title,
            reminder_dt=reminder_iso,
            page_id=work_id,
            tz_offset=tz_offset,
        )
        if not ok:
            await message.answer("⚠️ Не удалось запланировать напоминание — дата в прошлом?")
            _pending.pop(uid, None)
            return True

        def _save_reminder():
            from datetime import datetime as _dt
            rd = _dt.strptime(reminder_iso[:16], "%Y-%m-%dT%H:%M")
            with get_engine().begin() as conn:
                conn.execute(
                    t_works.update()
                    .where(t_works.c.id == int(work_id))
                    .values(reminder=rd)
                )

        await asyncio.to_thread(_save_reminder)
    except Exception as e:
        logger.error("_do_reschedule failed: %s", e)
        await message.answer("⚠️ Ошибка переноса напоминания.")
        _pending.pop(uid, None)
        return True

    _pending.pop(uid, None)
    disp = reminder_iso[:16].replace("T", " ")
    await message.answer(f"✅ Напоминание перенесено на {disp}")
    return True
