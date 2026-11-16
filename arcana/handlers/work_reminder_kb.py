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


from core.date_extract import parse_relative_time as _parse_relative_time  # noqa: E402  общий код (#267)


# ── callbacks ──────────────────────────────────────────────────────────────────

async def _handle_recurring_work_done(
    message: Message, work_id: str, work, title: str, tz_offset: int,
) -> str:
    """Повторяющаяся Работа завершена → сдвинуть на следующий цикл, НЕ mark_done.

    Аналог nexus ``_handle_recurring_deadline_done``, но:
    - БЕЗ ``update_task_streak`` — стрики сознательно исключены (ADR-0023);
    - без промежуточного «In progress» — у Работ Арканы статусы open/done/archived,
      recycled-работа возвращается в 'open' с будущими датами.

    Возвращает дату следующего вхождения ('YYYY-MM-DD') или '' если не удалось.
    """
    from core.date_extract import dt_to_local_wall, wall_to_aware
    from core.reminder_scheduler import shift_cycle
    from arcana.bot import arcana_reminder_flow
    from arcana.reminder_adapter import work_deadline_wall
    from arcana.repos.pg_works_repo import PgWorksRepo

    repeat = (work.repeat if work else "") or "Ежедневно"

    # ЕДИНАЯ логика сдвига серии (core.reminder_scheduler.shift_cycle) — та же, что у Nexus:
    # время дедлайна и напоминания сохраняется, «весь день» остаётся днём.
    cur_deadline = work_deadline_wall(work.deadline_dt, tz_offset) if (work and work.deadline_dt) else ""
    cur_reminder = dt_to_local_wall(work.reminder_dt, tz_offset) if (work and work.reminder_dt) else ""
    new_reminder, new_deadline = shift_cycle(
        repeat, (work.repeat_time if work else "") or "", cur_reminder, cur_deadline, tz_offset)

    await PgWorksRepo().reschedule_cycle(
        work_id,
        deadline=wall_to_aware(new_deadline, tz_offset) if new_deadline else None,
        reminder=wall_to_aware(new_reminder, tz_offset) if new_reminder else None,
    )

    try:
        arcana_reminder_flow.remove_jobs(work_id)
    except Exception:
        pass
    if new_reminder:
        try:
            # #306: рассылаем во все чаты владельца, не только в чат, где
            # нажали «Сделала» (см. work_preview.cb_work_save).
            from core.user_manager import get_user_id, get_tg_ids_for_user
            owner_user_id = await get_user_id(message.from_user.id)
            recipients = await get_tg_ids_for_user(owner_user_id or "")
            await arcana_reminder_flow.schedule_reminder(
                chat_id=message.chat.id, title=title, reminder_dt=new_reminder,
                page_id=work_id, tz_offset=tz_offset,
                recipients=recipients or None,
            )
        except Exception as e:
            logger.warning("_handle_recurring_work_done: schedule failed: %s", e)
    elif new_deadline:
        # Напоминания нет → дедлайн-пинг нового цикла (как Nexus, #69).
        try:
            from core.user_manager import get_user_id, get_tg_ids_for_user
            owner_user_id = await get_user_id(message.from_user.id)
            recipients = await get_tg_ids_for_user(owner_user_id or "")
            await arcana_reminder_flow.schedule_deadline_check(
                chat_id=message.chat.id, title=title, deadline_dt=new_deadline,
                page_id=work_id, tz_offset=tz_offset,
                recipients=recipients or None,
            )
        except Exception as e:
            logger.warning("_handle_recurring_work_done: deadline schedule failed: %s", e)

    return (new_deadline or new_reminder or "")[:10]


@router.callback_query(F.data.startswith("work_complete_"))
async def work_complete(call: CallbackQuery) -> None:
    work_id = call.data.split("_", 2)[2]
    logger.info("work_complete: work_id=%s uid=%s", work_id, call.from_user.id)

    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        repo = PgWorksRepo()
        work = await repo.find_by_id(work_id)
        is_recurring = bool(work and work.repeat and work.repeat != "Нет")
        if is_recurring:
            tz_offset = await _get_tz(call.from_user.id)
            title = _get_title(call.message.text or "") or (work.title if work else "Работа")
            nxt = await _handle_recurring_work_done(
                call.message, work_id, work, title, tz_offset,
            )
            await call.message.edit_reply_markup()
            await call.answer("✅ Выполнено!")
            tail = f"\n🔄 Повтор: {work.repeat.lower()} — следующий раз {nxt}" if nxt else ""
            await call.message.reply(f"💅 Готово!\n✅ {title} — выполнено{tail}")
            return
        await repo.mark_done(work_id)
    except Exception as e:
        logger.error("work_complete: failed: %s", e)
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
            role="parse", temperature=0,
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
        from core.user_manager import get_user_id, get_tg_ids_for_user
        import asyncio

        # #306: рассылаем во все чаты владельца, не только в чат, где
        # перенесли напоминание (см. work_preview.cb_work_save).
        owner_user_id = await get_user_id(uid)
        recipients = await get_tg_ids_for_user(owner_user_id or "")
        # Новое напоминание заменяет прежние jobs: дедлайн-пинг при reminder не нужен (#69).
        arcana_reminder_flow.remove_jobs(work_id)
        ok = await arcana_reminder_flow.schedule_reminder(
            chat_id=message.chat.id,
            title=title,
            reminder_dt=reminder_iso,
            page_id=work_id,
            tz_offset=tz_offset,
            recipients=recipients or None,
        )
        if not ok:
            await message.answer("⚠️ Не удалось запланировать напоминание — дата в прошлом?")
            _pending.pop(uid, None)
            return True

        def _save_reminder():
            from core.date_extract import wall_to_aware
            rd = wall_to_aware(reminder_iso[:16], int(tz_offset))
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
