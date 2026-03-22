"""nexus/handlers/tasks.py — Новый флоу с напоминаниями и дедлайнами"""
from __future__ import annotations

import json
import logging
import sqlite3 as _sqlite3
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from core.claude_client import ask_claude
from core.notion_client import tasks_active, log_error, page_create, _title, _select, _date, _status, update_page, db_query, get_notion
from core.layout import maybe_convert

logger = logging.getLogger("nexus.tasks")
MOSCOW_TZ = timezone(timedelta(hours=3))
router = Router()

CATEGORIES = [
    "🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба",
    "💰 Зарплата", "💳 Прочее", "👥 Люди",
]
PRACTICE_CATEGORIES = {"🕯️ Расходники", "🔮 Практика"}

# ── SQLite persistent pending ──────────────────────────────────────────────────
import os as _os
_PENDING_DB = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "../../pending_tasks.db")
_PENDING_TTL = 1800  # 30 minutes

def _pdb() -> _sqlite3.Connection:
    con = _sqlite3.connect(_PENDING_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pending "
        "(uid INTEGER PRIMARY KEY, data TEXT, ts REAL)"
    )
    con.commit()
    return con

def _pending_set(uid: int, data: dict) -> None:
    with _pdb() as con:
        con.execute(
            "INSERT OR REPLACE INTO pending (uid, data, ts) VALUES (?,?,?)",
            (uid, json.dumps(data, ensure_ascii=False), _time.time())
        )

def _pending_get(uid: int) -> Optional[dict]:
    with _pdb() as con:
        row = con.execute(
            "SELECT data, ts FROM pending WHERE uid=?", (uid,)
        ).fetchone()
    if not row:
        return None
    if _time.time() - row[1] > _PENDING_TTL:
        _pending_del(uid)
        return None
    return json.loads(row[0])

def _pending_del(uid: int) -> None:
    with _pdb() as con:
        con.execute("DELETE FROM pending WHERE uid=?", (uid,))

def _pending_pop(uid: int) -> Optional[dict]:
    data = _pending_get(uid)
    if data is not None:
        _pending_del(uid)
    return data

def _pending_has(uid: int) -> bool:
    return _pending_get(uid) is not None


# ── last_record: запоминаем последнюю созданную запись пользователя ─────────────
def _lrdb() -> _sqlite3.Connection:
    con = _sqlite3.connect(_PENDING_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS last_record "
        "(uid INTEGER PRIMARY KEY, db_type TEXT, page_id TEXT)"
    )
    con.commit()
    return con


def last_record_set(uid: int, db_type: str, page_id: str) -> None:
    """Сохранить последнюю запись пользователя. db_type: 'task' | 'finance'."""
    with _lrdb() as con:
        con.execute(
            "INSERT OR REPLACE INTO last_record (uid, db_type, page_id) VALUES (?,?,?)",
            (uid, db_type, page_id),
        )


def last_record_get(uid: int) -> Optional[tuple]:
    """Вернуть (db_type, page_id) или None."""
    with _lrdb() as con:
        row = con.execute(
            "SELECT db_type, page_id FROM last_record WHERE uid=?", (uid,)
        ).fetchone()
    return row  # (db_type, page_id) or None


# ── Global state ───────────────────────────────────────────────────────────────
_user_tz_offset: dict = {}
_scheduler = None
_bot: Optional[Bot] = None

class _HasPending(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and _pending_has(message.from_user.id)

# ── Scheduler ──────────────────────────────────────────────────────────────────
def init_scheduler(bot: Bot) -> None:
    global _scheduler, _bot
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _bot = bot
    _scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    _scheduler.start()
    logger.info("APScheduler started")


async def restore_reminders_on_startup() -> None:
    """Восстановить APScheduler jobs для задач с напоминаниями.

    Проход 1: задачи с будущим напоминанием — планируем как есть.
    Проход 2: повторяющиеся задачи с прошедшим напоминанием —
              сдвигаем до ближайшей будущей даты, обновляем Notion, планируем.
    """
    from core.config import config
    from core.user_manager import get_user
    from core.notion_client import db_query
    import os

    db_id = os.environ.get("NOTION_DB_TASKS") or config.nexus.db_tasks
    if not db_id or not _scheduler or not _bot:
        logger.warning("restore_reminders_on_startup: scheduler/bot/db not ready")
        return

    now_utc = datetime.now(timezone.utc)
    now_utc_str = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    restored = 0

    for tg_id in config.allowed_ids:
        try:
            user_data = await get_user(tg_id)
            if not user_data:
                continue
            user_notion_id = user_data.get("notion_page_id", "")
            tz_offset = await _get_user_tz(tg_id)
            user_filter = {"property": "🪪 Пользователи", "relation": {"contains": user_notion_id}} if user_notion_id else None

            # ── Проход 1: будущие напоминания ────────────────────────────────────
            filter1: dict = {"and": [
                {"property": "Напоминание", "date": {"after": now_utc_str}},
                {"property": "Статус", "status": {"does_not_equal": "Done"}},
            ]}
            if user_filter:
                filter1["and"].append(user_filter)

            for page in await db_query(db_id, filter_obj=filter1, page_size=100):
                try:
                    props = page["properties"]
                    task_id = page["id"]
                    title_parts = props.get("Задача", {}).get("title", [])
                    title = title_parts[0]["plain_text"] if title_parts else "Задача"
                    reminder_start = (props.get("Напоминание", {}).get("date") or {}).get("start", "")
                    if reminder_start:
                        await _schedule_reminder(tg_id, title, reminder_start[:16], task_id, tz_offset)
                        restored += 1
                except Exception as e:
                    logger.error("restore pass1: task %s error: %s", page.get("id"), e)

            # ── Проход 2: повторяющиеся задачи с прошедшим напоминанием ─────────
            filter2: dict = {"and": [
                {"property": "Статус", "status": {"does_not_equal": "Done"}},
                {"property": "Повтор", "select": {"does_not_equal": "Нет"}},
                {"property": "Напоминание", "date": {"before": now_utc_str}},
            ]}
            if user_filter:
                filter2["and"].append(user_filter)

            for page in await db_query(db_id, filter_obj=filter2, page_size=100):
                try:
                    props = page["properties"]
                    task_id = page["id"]
                    title_parts = props.get("Задача", {}).get("title", [])
                    title = title_parts[0]["plain_text"] if title_parts else "Задача"
                    repeat = (props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
                    if repeat == "Нет":
                        continue
                    reminder_start = (props.get("Напоминание", {}).get("date") or {}).get("start", "")
                    if not reminder_start:
                        continue

                    # Сдвигаем до ближайшей будущей даты (может потребоваться несколько циклов)
                    new_reminder = reminder_start[:16]
                    for _ in range(400):  # защита от зацикливания
                        new_reminder = _next_cycle_date(new_reminder, repeat, tz_offset)
                        try:
                            rem_dt = datetime.strptime(new_reminder[:16], "%Y-%m-%dT%H:%M").replace(
                                tzinfo=timezone(timedelta(hours=tz_offset))
                            )
                        except ValueError:
                            break
                        if rem_dt > now_utc:
                            break

                    # Обновляем дедлайн тоже если он есть
                    deadline_start = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
                    update_props: dict = {"Напоминание": _date(new_reminder)}
                    if deadline_start:
                        new_deadline = deadline_start[:16]
                        for _ in range(400):
                            new_deadline = _next_cycle_date(new_deadline, repeat, tz_offset)
                            try:
                                dl_dt = datetime.strptime(new_deadline[:10], "%Y-%m-%d").replace(
                                    tzinfo=timezone(timedelta(hours=tz_offset))
                                )
                            except ValueError:
                                break
                            if dl_dt > now_utc:
                                break
                        update_props["Дедлайн"] = _date(new_deadline[:10])

                    await update_page(task_id, update_props)
                    await _schedule_reminder(tg_id, title, new_reminder, task_id, tz_offset)
                    logger.info("restore pass2: rescheduled '%s' repeat=%s next=%s", title, repeat, new_reminder)
                    restored += 1
                except Exception as e:
                    logger.error("restore pass2: task %s error: %s", page.get("id"), e)

        except Exception as e:
            logger.error("restore_reminders_on_startup: tg_id=%s error: %s", tg_id, e)

    logger.info("restore_reminders_on_startup: restored %d reminder jobs", restored)


def _now(uid: int = 0) -> datetime:
    offset = _user_tz_offset.get(uid, 3)
    return datetime.now(timezone(timedelta(hours=offset)))

async def _schedule_reminder(chat_id: int, title: str, reminder_dt: str, task_id: str, tz_offset: int = 3) -> None:
    if not _scheduler or not _bot:
        return
    try:
        dt = datetime.strptime(reminder_dt, "%Y-%m-%dT%H:%M").replace(
            tzinfo=timezone(timedelta(hours=tz_offset))
        )
        if dt <= _now():
            logger.warning("Reminder in the past: %s", reminder_dt)
            return

        async def send_reminder() -> None:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Сделано!", callback_data=f"task_complete_{task_id}"),
                InlineKeyboardButton(text="❌ Не сделал", callback_data=f"task_failed_{task_id}"),
            ]])
            await _bot.send_message(
                chat_id, 
                f"🔔 <b>Напоминание:</b> {title}\n\nСделано?",
                parse_mode="HTML",
                reply_markup=kb
            )

        job_id = f"reminder_{task_id}" if task_id else f"rem_{chat_id}_{title[:15]}_{reminder_dt}"
        logger.info("scheduling reminder: task_id=%s chat_id=%s job_id=%s callback_data=task_complete_%s", task_id, chat_id, job_id, task_id)
        _scheduler.add_job(send_reminder, trigger="date", run_date=dt,
                           id=job_id, replace_existing=True)
        logger.info("Reminder scheduled: %s at %s", title, dt)
    except Exception as e:
        logger.error("Schedule reminder error: %s", e)

async def _schedule_deadline_check(chat_id: int, title: str, deadline_dt: str, task_id: str, tz_offset: int = 3) -> None:
    if not _scheduler or not _bot:
        return
    try:
        dt = datetime.strptime(deadline_dt, "%Y-%m-%dT%H:%M" if "T" in deadline_dt else "%Y-%m-%d").replace(
            tzinfo=timezone(timedelta(hours=tz_offset))
        )
        if dt <= _now():
            logger.warning("Deadline in the past: %s", deadline_dt)
            return

        async def check_deadline() -> None:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Выполнено!", callback_data=f"task_complete_{task_id}"),
                InlineKeyboardButton(text="⏳ Отложить", callback_data=f"task_reschedule_{task_id}"),
            ]])
            await _bot.send_message(
                chat_id,
                f"⏰ <b>Дедлайн:</b> {title}\n\nСделал?",
                parse_mode="HTML",
                reply_markup=kb
            )

        job_id = f"deadline_{task_id}" if task_id else f"deadline_{chat_id}_{title[:15]}_{deadline_dt}"
        logger.info("scheduling deadline: task_id=%s chat_id=%s job_id=%s callback_data=task_complete_%s", task_id, chat_id, job_id, task_id)
        _scheduler.add_job(check_deadline, trigger="date", run_date=dt,
                           id=job_id, replace_existing=True)
        logger.info("Deadline check scheduled: %s at %s", title, dt)
    except Exception as e:
        logger.error("Schedule deadline error: %s", e)

async def _get_user_tz(uid: int) -> int:
    if uid in _user_tz_offset:
        return _user_tz_offset[uid]
    from core.notion_client import memory_get
    stored = await memory_get(f"tz_{uid}")
    if stored:
        try:
            offset = int(stored)
            _user_tz_offset[uid] = offset
            return offset
        except Exception:
            pass
    return 3

_CITY_TZ = {
    # Россия
    "москва": 3, "мск": 3, "московск": 3,
    "спб": 3, "санкт-петербург": 3, "питер": 3, "петербург": 3,
    "калининград": 2,
    "самара": 4, "удмуртия": 5, "ижевск": 5,
    "екатеринбург": 5, "екб": 5, "ебург": 5, "свердловск": 5, "уфа": 5,
    "челябинск": 5, "тюмень": 5, "башкирия": 5, "пермь": 5,
    "омск": 6,
    "новосибирск": 7, "новосиб": 7, "красноярск": 7, "томск": 7, "барнаул": 7,
    "иркутск": 8, "улан-удэ": 8,
    "якутск": 9, "хабаровск": 10, "владивосток": 10, "магадан": 11,
    "сахалин": 11, "камчатка": 12,
    # Другие
    "дубай": 4, "абу-даби": 4,
    "берлин": 1, "варшава": 1, "рим": 1, "париж": 1,
    "лондон": 0,
    "бангкок": 7, "токио": 9, "сеул": 9, "пекин": 8,
}


async def _update_user_tz(message: Message, text: str) -> None:
    from core.notion_client import memory_set
    uid = message.from_user.id
    text_low = text.lower()

    # Сначала пробуем словарь городов (быстро, без API)
    offset = None
    for city, tz in _CITY_TZ.items():
        if city in text_low:
            offset = tz
            break

    # Потом пробуем UTC±X паттерн
    if offset is None:
        import re as _re
        m = _re.search(r"utc\s*([+-]?\d+)", text_low)
        if m:
            try:
                offset = int(m.group(1))
            except ValueError:
                pass

    # Крайний случай — спрашиваем Claude
    if offset is None:
        system = """Пользователь указывает часовой пояс. Ответь ТОЛЬКО числом — смещение UTC в часах.
Примеры: Екатеринбург=5, Москва=3, Спб=3, Дубай=4, Берлин=1, Бангкок=7, Токио=9, Новосибирск=7, Иркутск=8
Если не понял → 3"""
        try:
            raw = await ask_claude(text, system=system, max_tokens=5, model="claude-haiku-4-5-20251001")
            offset = int(raw.strip().split()[0])
        except Exception:
            offset = 3

    _user_tz_offset[uid] = offset
    await memory_set(f"tz_{uid}", str(offset), "Настройки")
    sign = "+" if offset >= 0 else ""
    await message.answer(f"🕐 Часовой пояс обновлён: UTC{sign}{offset}")

# ── Relative time parser ───────────────────────────────────────────────────────

import re as _re

_REL_TIME_RE = _re.compile(
    r"через\s+(\d+)\s*(мин[а-я]*|час[а-я]*|ч\b|дн[а-я]*|день|дней)",
    _re.IGNORECASE,
)


def _parse_relative_time(text: str, tz_offset: int) -> Optional[str]:
    """Парсить 'через N минут/часов/дней' → 'YYYY-MM-DDTHH:MM'.

    Возвращает строку или None если паттерн не найден.
    Не использует Claude — вычисляет offset от datetime.now().
    """
    m = _REL_TIME_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    if unit.startswith("мин"):
        result = now + timedelta(minutes=n)
    elif unit.startswith("ч") or unit.startswith("час"):
        result = now + timedelta(hours=n)
    else:  # день / дней / дня
        result = now + timedelta(days=n)
    return result.strftime("%Y-%m-%dT%H:%M")


import calendar as _calendar


def _next_cycle_date(current_date_str: str, repeat: str, tz_offset: int = 3) -> str:
    """Вычислить дату следующего цикла для повторяющейся задачи.

    Если входная строка содержит время (YYYY-MM-DDTHH:MM) — время сохраняется.
    Возвращает YYYY-MM-DD или YYYY-MM-DDTHH:MM.
    """
    has_time = "T" in (current_date_str or "")
    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    if current_date_str:
        try:
            base = datetime.strptime(current_date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            base = now.date()
    else:
        base = now.date()

    if repeat == "Ежедневно":
        next_date = base + timedelta(days=1)
    elif repeat == "Еженедельно":
        next_date = base + timedelta(weeks=1)
    elif repeat == "Ежемесячно":
        month = base.month + 1
        year = base.year
        if month > 12:
            month = 1
            year += 1
        try:
            next_date = base.replace(year=year, month=month)
        except ValueError:
            last_day = _calendar.monthrange(year, month)[1]
            next_date = base.replace(year=year, month=month, day=last_day)
    else:
        next_date = base + timedelta(days=1)

    result = next_date.strftime("%Y-%m-%d")
    if has_time:
        time_part = current_date_str.split("T")[1][:5]
        result = result + "T" + time_part
    return result


async def _handle_recurring_task_reset(
    message: Message,
    task_id: str,
    task_props: dict,
    repeat: str,
    title: str,
    uid: int = 0,
) -> None:
    """Сбросить повторяющуюся задачу: сдвинуть дедлайн/напоминание, статус → Not started."""
    tz_offset = await _get_user_tz(uid)

    deadline_prop = task_props.get("Дедлайн", {}).get("date") or {}
    current_deadline = deadline_prop.get("start", "")

    reminder_prop = task_props.get("Напоминание", {}).get("date") or {}
    current_reminder = reminder_prop.get("start", "")

    new_deadline = _next_cycle_date(current_deadline, repeat, tz_offset)

    update_props = {"Статус": _status("Not started")}
    if current_deadline:
        update_props["Дедлайн"] = _date(new_deadline[:10])
    if current_reminder:
        new_reminder = _next_cycle_date(current_reminder, repeat, tz_offset)
        update_props["Напоминание"] = _date(new_reminder)

    try:
        await update_page(task_id, update_props)
        next_display = new_deadline[:10]

        # Пересоздать scheduler jobs с новыми датами
        chat_id = message.chat.id
        if _scheduler:
            # Напоминание
            if current_reminder:
                new_reminder = _next_cycle_date(current_reminder, repeat, tz_offset)
                try:
                    _scheduler.remove_job(f"reminder_{task_id}")
                except Exception:
                    pass
                await _schedule_reminder(chat_id, title, new_reminder, task_id, tz_offset)
            # Дедлайн
            if current_deadline:
                try:
                    _scheduler.remove_job(f"deadline_{task_id}")
                except Exception:
                    pass
                await _schedule_deadline_check(chat_id, title, new_deadline, task_id, tz_offset)

        await message.answer(f"🔄 Повторяющаяся задача сброшена. Следующий раз: {next_display}")
    except Exception as e:
        logger.error("_handle_recurring_task_reset error: %s", e)
        await message.answer("⚠️ Ошибка обновления повторяющейся задачи.")


# ── Handlers ───────────────────────────────────────────────────────────────────

_REMIND_WORDS = {"напомни", "напоминай", "remind", "напомнить", "напомни мне"}


def _has_remind_word(text: str) -> bool:
    """Проверить что в тексте есть слово-триггер напоминания."""
    low = text.lower()
    return any(w in low for w in _REMIND_WORDS)


async def handle_task_parsed(message: Message, data: dict) -> None:
    """Парсим задачу. Если есть 'напомни' — уже знаем reminder, спрашиваем дедлайн.
    Иначе — спрашиваем когда напомнить."""
    uid = message.from_user.id
    logger.info("handle_task_parsed: title=%s deadline=%s", data.get("title"), data.get("deadline"))

    if not data.get("title"):
        await message.answer("⚠️ Не нашёл название задачи.")
        return

    data.setdefault("for_practice", False)

    repeat = data.get("repeat") or "Нет"
    if repeat and repeat != "Нет":
        repeat_time_str = data.get("repeat_time") or "09:00"
        try:
            h, m = map(int, repeat_time_str.split(":"))
        except Exception:
            h, m = 9, 0
        tz_offset = await _get_user_tz(uid)
        user_tz = timezone(timedelta(hours=tz_offset))
        now = datetime.now(user_tz)
        first_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if first_run <= now:
            first_run = first_run + timedelta(days=1)
        # Дедлайн перезаписываем только если пользователь не задал явный (напр. "до пт")
        if not data.get("deadline"):
            data["deadline"] = first_run.strftime("%Y-%m-%dT%H:%M")
        data["reminder_time"] = first_run.strftime("%Y-%m-%dT%H:%M")
        logger.info("handle_task_parsed: repeat=%s → first_run=%s deadline=%s", repeat, first_run, data["deadline"])
        await _do_save_task(message, data, chat_id=message.chat.id, uid=uid)
        return

    # Определяем оригинальный текст из message
    original_text = message.text or ""

    has_remind = _has_remind_word(original_text)

    # Pre-filter: "через N мин/часов/дней" → вычислить до Claude
    # Баг Claude: "через 2 мин" → "00:02" вместо now+2min
    tz_offset = await _get_user_tz(uid)
    rel_match = _REL_TIME_RE.search(original_text)
    if rel_match:
        relative_time = _parse_relative_time(original_text, tz_offset)
        unit = rel_match.group(2).lower()
        if unit.startswith("мин") or unit.startswith("ч"):
            # минуты / часы → reminder_time (никогда не дедлайн)
            logger.info("handle_task_parsed: pre-filter relative reminder=%s", relative_time)
            data["reminder_time"] = relative_time
            data["deadline"] = None  # сброс неверного дедлайна от Claude
            if has_remind:
                # reminder уже известен — сразу спрашиваем только дедлайн
                reminder_display = relative_time.replace("T", " ")
                msg = await message.answer(
                    f"📌 <b>{data.get('title')}</b>\n"
                    f"🏷 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n"
                    f"🔔 Напомню: {reminder_display}\n\n"
                    f"<b>📅 Дедлайн?</b>",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✅ Тот же день", callback_data="task_deadline_same"),
                        InlineKeyboardButton(text="📅 +1 день", callback_data="task_deadline_plus1"),
                    ], [
                        InlineKeyboardButton(text="📅 +3 дня", callback_data="task_deadline_plus3"),
                        InlineKeyboardButton(text="🚫 Без дедлайна", callback_data="task_save"),
                    ], [
                        InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
                    ]])
                )
                data["msg_id"] = msg.message_id
                data["_awaiting_deadline"] = True
                _pending_set(uid, data)
                return
        else:
            # дни → дедлайн
            logger.info("handle_task_parsed: pre-filter relative deadline=%s", relative_time)
            data["deadline"] = relative_time

    # "напомни [дата из Claude]" — reminder_time = deadline, спросить дедлайн
    if has_remind and data.get("deadline"):
        data["reminder_time"] = data["deadline"]
        data["deadline"] = None

        # Если Claude вернул только дату без времени → спрашиваем время сразу
        if "T" not in data["reminder_time"]:
            msg_obj = await message.answer(
                f"📌 <b>{data.get('title')}</b>\n"
                f"🏷 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n\n"
                f"<b>⏰ В какое время напомнить?</b>\n"
                f"Примеры: <code>в 10:00</code>, <code>в 18:30</code>, <code>через 2 часа</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
                ]])
            )
            data["msg_id"] = msg_obj.message_id
            _pending_set(uid, data)
            return

        reminder_display = data["reminder_time"].replace("T", " ")
        msg = await message.answer(
            f"📌 <b>{data.get('title')}</b>\n"
            f"🏷 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n"
            f"🔔 Напомню: {reminder_display}\n\n"
            f"<b>📅 Дедлайн?</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Тот же день", callback_data="task_deadline_same"),
                InlineKeyboardButton(text="📅 +1 день", callback_data="task_deadline_plus1"),
            ], [
                InlineKeyboardButton(text="📅 +3 дня", callback_data="task_deadline_plus3"),
                InlineKeyboardButton(text="🚫 Без дедлайна", callback_data="task_save"),
            ], [
                InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
            ]])
        )
        data["msg_id"] = msg.message_id
        data["_awaiting_deadline"] = True
        _pending_set(uid, data)
        return

    # reminder_time уже установлен (pre-filter или classifier) — не спрашивать время, сразу дедлайн
    if data.get("reminder_time"):
        # Если только дата без времени — сначала спрашиваем время
        if "T" not in data["reminder_time"]:
            msg_obj = await message.answer(
                f"📌 <b>{data.get('title')}</b>\n"
                f"🏷 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n\n"
                f"<b>⏰ В какое время напомнить?</b>\n"
                f"Примеры: <code>в 10:00</code>, <code>в 18:30</code>, <code>через 2 часа</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
                ]])
            )
            data["msg_id"] = msg_obj.message_id
            _pending_set(uid, data)
            return
        reminder_display = data["reminder_time"].replace("T", " ")
        msg = await message.answer(
            f"📌 <b>{data.get('title')}</b>\n"
            f"🏷 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n"
            f"🔔 Напомню: {reminder_display}\n\n"
            f"<b>📅 Дедлайн?</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Тот же день", callback_data="task_deadline_same"),
                InlineKeyboardButton(text="📅 +1 день", callback_data="task_deadline_plus1"),
            ], [
                InlineKeyboardButton(text="📅 +3 дня", callback_data="task_deadline_plus3"),
                InlineKeyboardButton(text="🚫 Без дедлайна", callback_data="task_save"),
            ], [
                InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
            ]])
        )
        data["msg_id"] = msg.message_id
        data["_awaiting_deadline"] = True
        _pending_set(uid, data)
        return

    # Нет "напомни" и нет reminder_time → один объединённый вопрос
    deadline_str = data.get("deadline") or ""
    deadline_hint = deadline_str.replace("T", " ") if deadline_str else "не указан"

    msg = await message.answer(
        f"📌 <b>{data.get('title')}</b>\n"
        f"🗂 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n"
        f"📅 Дедлайн: {deadline_hint}\n"
        f"🔔 Напоминание: нет\n\n"
        f"❓ Уточни:\n"
        f"— Когда сделать? («завтра», «15 марта», «через 2 дня»)\n"
        f"— Напомнить? («в 10:00», «за час», «завтра в 15»)\n\n"
        f"<i>Или нажми «Сохранить» как есть</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Сохранить", callback_data="task_save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
        ]])
    )

    data["msg_id"] = msg.message_id
    data["_awaiting_combined"] = True
    _pending_set(uid, data)

async def _show_task_confirm(message: Message, pending: dict, uid: int) -> None:
    """Показать карточку подтверждения задачи (редактируем старое сообщение)."""
    is_practice_cat = pending.get("category", "") in PRACTICE_CATEGORIES
    deadline_display = (pending.get("deadline") or "не указана").replace("T", " ")
    reminder_display = (pending.get("reminder_time") or "нет").replace("T", " ")

    text_content = (
        f"📌 <b>{pending['title']}</b>\n"
        f"🏷 {pending.get('category', '?')} · {pending.get('priority', 'Средний')}\n"
        f"📅 Дедлайн: {deadline_display}\n"
        f"🔔 Напомню: {reminder_display}\n\n"
    )
    if is_practice_cat:
        text_content += "🕯️ Это для практики (Arcana) или для себя?"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔮 Да, для практики", callback_data="task_practice"),
            InlineKeyboardButton(text="🏠 Нет, для себя", callback_data="task_personal"),
        ], [
            InlineKeyboardButton(text="✅ Сохранить", callback_data="task_save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
        ]])
    else:
        text_content += "<i>Всё верно?</i>"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Сохранить", callback_data="task_save"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
        ]])

    msg_id = pending.get("msg_id")
    if msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id, message_id=msg_id,
                text=text_content, parse_mode="HTML", reply_markup=kb,
            )
        except Exception:
            await message.answer(text_content, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text_content, parse_mode="HTML", reply_markup=kb)


async def handle_task_clarification(message: Message) -> None:
    """Обработка уточнений по задаче - парсим напоминание (и дедлайн в combined-режиме)."""
    uid = message.from_user.id
    pending = _pending_get(uid)
    if not pending:
        return

    text = maybe_convert(message.text.strip())

    # Combined-режим: один вопрос про дедлайн + напоминание
    if pending.get("_awaiting_combined"):
        await _handle_combined_clarification(message, text, pending, uid)
        return

    try:
        tz_offset = await _get_user_tz(uid)

        # Быстрый парсер для "через N мин/часов/дней" — не доверяем Claude
        relative = _parse_relative_time(text, tz_offset)
        if relative:
            logger.info("handle_task_clarification: relative time parsed locally: %s", relative)
            pending["reminder_time"] = relative
            _pending_set(uid, pending)
            await _show_task_confirm(message, pending, uid)
            return

        now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")
        deadline_str = pending.get("deadline") or "не указана"

        now_dt = datetime.now(timezone(timedelta(hours=tz_offset)))
        is_night = now_dt.hour < 5
        tomorrow_note = "ВАЖНО: сейчас ночь (до 05:00) — 'завтра' означает СЕГОДНЯ (тот же календарный день)!" if is_night else ""

        system = f"""Пользователь указывает когда напомнить. Парсь и верни ТОЛЬКО JSON без markdown:
{{"reminder_time": "YYYY-MM-DDTHH:MM или null"}}

Правила:
- "в 10:00" → в 10:00 (если прошло то завтра)
- "завтра в 15:00" → завтра в 15:00
{tomorrow_note}

Сейчас: {now_str} (UTC+{tz_offset})
Дедлайн: {deadline_str}"""
        
        raw = await ask_claude(text, system=system, max_tokens=100, model="claude-haiku-4-5-20251001")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        logger.info("Claude returned: %s", raw)
        parsed = json.loads(raw)
        
        if parsed.get("reminder_time"):
            pending["reminder_time"] = parsed["reminder_time"]
            _pending_set(uid, pending)
            logger.info("parsed reminder_time: %s", pending["reminder_time"])
            await _show_task_confirm(message, pending, uid)
            return
        else:
            logger.warning("Claude returned no reminder_time: %s", parsed)
            await message.answer("⏰ Не понял когда напомнить. Укажи время:\n<code>завтра в 10:00</code>, <code>в 15:00</code>, <code>через 2 часа</code>")
            return

    except Exception as e:
        logger.error("parse reminder error: %s", e)
        await message.answer("❌ Ошибка при обработке. Попробуй ещё раз")


async def _handle_combined_clarification(message: Message, text: str, pending: dict, uid: int) -> None:
    """Парсим и дедлайн и напоминание из одного сообщения пользователя."""
    try:
        tz_offset = await _get_user_tz(uid)

        # Быстрый парсер для "через N мин/часов/дней" → treat as reminder
        relative = _parse_relative_time(text, tz_offset)
        if relative:
            logger.info("_handle_combined_clarification: relative time=%s", relative)
            pending["reminder_time"] = relative
            pending.pop("_awaiting_combined", None)
            _pending_set(uid, pending)
            await _show_task_confirm(message, pending, uid)
            return

        now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")
        now_dt = datetime.now(timezone(timedelta(hours=tz_offset)))
        is_night = now_dt.hour < 5
        tomorrow_note = "ВАЖНО: сейчас ночь (до 05:00) — 'завтра' означает СЕГОДНЯ (тот же календарный день)!" if is_night else ""

        system = f"""Пользователь указывает дедлайн и/или напоминание для задачи. Парсь и верни ТОЛЬКО JSON без markdown:
{{"deadline": "YYYY-MM-DD или null", "reminder_time": "YYYY-MM-DDTHH:MM или null"}}

Правила:
- "завтра в 15" → deadline=завтра, reminder_time=завтра в 15:00
- "в пятницу" → deadline=пятница, reminder_time=null
- "в 10:00" → deadline=null, reminder_time=сегодня или завтра в 10:00
- "через 2 дня" → deadline=через 2 дня, reminder_time=null
{tomorrow_note}
Сейчас: {now_str} (UTC+{tz_offset})"""

        raw = await ask_claude(text, system=system, max_tokens=150, model="claude-haiku-4-5-20251001")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        logger.info("_handle_combined_clarification Claude returned: %s", raw)
        parsed = json.loads(raw)

        if parsed.get("deadline"):
            pending["deadline"] = parsed["deadline"]
        if parsed.get("reminder_time"):
            pending["reminder_time"] = parsed["reminder_time"]

        pending.pop("_awaiting_combined", None)
        _pending_set(uid, pending)
        await _show_task_confirm(message, pending, uid)

    except Exception as e:
        logger.error("_handle_combined_clarification error: %s", e)
        await message.answer("❌ Ошибка при обработке. Попробуй ещё раз")


# ── Callbacks ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("task_deadline_"))
async def task_deadline_choice(call: CallbackQuery) -> None:
    """Выбор дедлайна после того как напоминание уже задано (поток 'напомни')."""
    from datetime import timedelta
    uid = call.from_user.id
    d = _pending_get(uid)
    if not d:
        await call.answer("Нет данных.")
        return

    choice = call.data  # task_deadline_same / task_deadline_plus1 / task_deadline_plus3
    reminder_time = d.get("reminder_time", "")

    if reminder_time and "T" in reminder_time:
        reminder_date = reminder_time.split("T")[0]
    elif reminder_time:
        reminder_date = reminder_time[:10]
    else:
        reminder_date = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    try:
        base_dt = datetime.strptime(reminder_date, "%Y-%m-%d")
    except ValueError:
        base_dt = datetime.now(MOSCOW_TZ)

    if choice == "task_deadline_same":
        deadline = base_dt.strftime("%Y-%m-%d")
    elif choice == "task_deadline_plus1":
        deadline = (base_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    elif choice == "task_deadline_plus3":
        deadline = (base_dt + timedelta(days=3)).strftime("%Y-%m-%d")
    else:
        deadline = None

    if deadline:
        d["deadline"] = deadline
    d.pop("_awaiting_deadline", None)
    then_ask_remind = d.pop("_then_ask_remind", False)
    _pending_set(uid, d)

    await call.message.edit_reply_markup()
    await call.answer()

    # Поток "без напомни": после дедлайна → спросить когда напомнить
    if then_ask_remind:
        deadline_display = (d.get("deadline") or "без даты").replace("T", " ")
        msg = await call.message.answer(
            f"📌 <b>{d.get('title')}</b>\n"
            f"🏷 {d.get('category', '?')} · {d.get('priority', 'Средний')}\n"
            f"📅 Дедлайн: {deadline_display}\n\n"
            f"<b>⏰ Когда напомнить?</b>\n"
            f"Примеры: <code>завтра в 10:00</code>, <code>в 15:00</code>, <code>через 2 часа</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚫 Без напоминания", callback_data="task_save"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
            ]])
        )
        d["msg_id"] = msg.message_id
        _pending_set(uid, d)
        return

    await _do_save_task(call.message, d, chat_id=call.message.chat.id, uid=uid)
    _pending_del(uid)


@router.callback_query(F.data == "task_practice")
async def task_practice(call: CallbackQuery) -> None:
    uid = call.from_user.id
    d = _pending_get(uid)
    if d:
        d["for_practice"] = True
        _pending_set(uid, d)
    await call.message.edit_reply_markup()
    await call.answer("🔮 Для практики")

@router.callback_query(F.data == "task_personal")
async def task_personal(call: CallbackQuery) -> None:
    uid = call.from_user.id
    d = _pending_get(uid)
    if d:
        d["for_practice"] = False
        _pending_set(uid, d)
    await call.message.edit_reply_markup()
    await call.answer("🏠 Для себя")

@router.callback_query(F.data == "task_save")
async def task_save(call: CallbackQuery) -> None:
    uid = call.from_user.id
    data = _pending_pop(uid)
    if not data:
        await call.answer("Нет данных.")
        return
    await call.message.edit_reply_markup()
    await _do_save_task(call.message, data, chat_id=call.message.chat.id, uid=uid)
    await call.answer()

@router.callback_query(F.data == "task_cancel")
async def task_cancel(call: CallbackQuery) -> None:
    _pending_del(call.from_user.id)
    await call.message.edit_text("❌ Отмена.")
    await call.answer()

_DONE_PHRASES = [
    "🎉 Кай, ты просто огонь!",
    "✨ Ты просто магия!",
    "🔥 Красавица, сделала!",
    "💅 Готово, как всегда на высоте",
    "⚡ Кай справилась!",
    "🌟 Вот это продуктивность!",
]


@router.callback_query(F.data.startswith("task_complete_"))
async def task_complete(call: CallbackQuery) -> None:
    logger.info("task_complete callback: data=%s uid=%s", call.data, call.from_user.id)
    import random
    from core.notion_client import update_task_status
    task_id = call.data.split("_", 2)[2]
    logger.info("task_complete: task_id=%s", task_id)

    result = await update_task_status(task_id, "Done")
    if result:
        # Получить название задачи из текста сообщения
        msg_text = call.message.text or ""
        task_title = ""
        if "Напоминание:" in msg_text:
            task_title = msg_text.split("Напоминание:")[1].strip().split("\n")[0].strip()
        elif "Дедлайн:" in msg_text:
            task_title = msg_text.split("Дедлайн:")[1].strip().split(".")[0].strip()

        phrase = random.choice(_DONE_PHRASES)
        title_line = f"\n✅ {task_title} — выполнено" if task_title else "\n✅ Выполнено"

        await call.message.edit_reply_markup()
        await call.answer("✅ Записано!")
        await call.message.reply(f"{phrase}{title_line}")
    else:
        await call.answer("⚠️ Ошибка обновления", show_alert=True)

@router.callback_query(F.data.startswith("task_failed_"))
async def task_failed(call: CallbackQuery) -> None:
    logger.info("task_failed callback: data=%s uid=%s", call.data, call.from_user.id)
    uid = call.from_user.id
    task_id = call.data.split("_", 2)[2]
    logger.info("task_failed: task_id=%s", task_id)
    
    # Получаем название из сообщения (если есть)
    msg_text = call.message.text or ""
    task_title = ""
    if "Напоминание:" in msg_text:
        task_title = msg_text.split("Напоминание:")[1].strip().split("\n")[0].strip()
    
    _pending_set(uid, {"task_id": task_id, "action": "reschedule", "title": task_title})
    await call.message.edit_reply_markup()
    await call.message.answer(
        "⏰ <b>Когда напомнить снова?</b>\n"
        "Примеры: <code>завтра в 10:00</code>, <code>через 2 часа</code>, <code>в понедельник</code>"
    )

@router.callback_query(F.data.startswith("task_reschedule_"))
async def task_reschedule(call: CallbackQuery) -> None:
    logger.info("task_reschedule callback: data=%s uid=%s", call.data, call.from_user.id)
    uid = call.from_user.id
    task_id = call.data.split("_", 2)[2]
    logger.info("task_reschedule: task_id=%s", task_id)
    
    # Получаем название из сообщения (если есть)
    msg_text = call.message.text or ""
    task_title = ""
    if "Дедлайн:" in msg_text:
        task_title = msg_text.split("Дедлайн:")[1].strip().split(".")[0].strip()
    
    _pending_set(uid, {"task_id": task_id, "action": "reschedule", "title": task_title})
    await call.message.edit_reply_markup()
    await call.message.answer(
        "⏰ <b>Когда напомнить снова?</b>\n"
        "Примеры: <code>завтра в 10:00</code>, <code>через 2 часа</code>, <code>в понедельник</code>"
    )

async def handle_reschedule_reminder(message: Message) -> None:
    """Обработка переноса напоминания."""
    from core.notion_client import db_query
    from core.config import config
    from core.layout import maybe_convert
    uid = message.from_user.id
    pending = _pending_get(uid)
    
    if not pending or pending.get("action") != "reschedule":
        return
    
    task_id = pending.get("task_id")
    if not task_id:
        return
    
    try:
        # Получить название из Notion если нет в pending
        task_title = pending.get("title") or f"Задача #{task_id[:8]}"
        if not pending.get("title"):
            try:
                pages = await db_query(config.nexus.db_tasks, page_size=1)
                for page in pages:
                    if task_id in page.get("id", ""):
                        title_parts = page.get("properties", {}).get("Задача", {}).get("title", [])
                        if title_parts:
                            task_title = title_parts[0]["plain_text"]
                        break
            except:
                pass
        
        tz_offset = await _get_user_tz(uid)
        text = maybe_convert(message.text)

        # Быстрый парсер для "через N мин/часов/дней"
        relative = _parse_relative_time(text, tz_offset)
        if relative:
            logger.info("handle_reschedule_reminder: relative time parsed locally: %s", relative)
            await _schedule_reminder(message.chat.id, task_title, relative, task_id, tz_offset)
            _pending_del(uid)
            await message.answer(f"✅ Напоминание перенесено на {relative.replace('T', ' ')}")
            return

        now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")

        system = f"""Пользователь указывает новое напоминание. Парсь и верни ТОЛЬКО JSON без markdown:
{{"reminder_time": "YYYY-MM-DDTHH:MM"}}

Правила:
- "завтра в 10:00" → завтра в 10:00
- "в понедельник" → в понедельник в 09:00

Сейчас: {now_str} (МСК, UTC+{tz_offset})"""

        raw = await ask_claude(text, system=system, max_tokens=100, model="claude-haiku-4-5-20251001")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        
        if parsed.get("reminder_time"):
            reminder_time = parsed["reminder_time"]
            await _schedule_reminder(message.chat.id, task_title, reminder_time, task_id, tz_offset)
            _pending_del(uid)
            await message.answer(f"✅ Напоминание перенесено на {reminder_time.replace('T', ' ')}")
        else:
            await message.answer("❌ Не смог парсить дату. Попробуй ещё раз")
    except Exception as e:
        logger.error("handle_reschedule error: %s", e)
        await message.answer("⚠️ Ошибка при обработке")

# ── Save task ──────────────────────────────────────────────────────────────────

async def _do_save_task(message: Message, data: dict, chat_id: int = None, uid: int = 0) -> None:
    from core.config import config
    from core.notion_client import match_select, _relation

    from core.option_helper import format_option
    db_id = config.nexus.db_tasks
    real_priority = await match_select(db_id, "Приоритет", data.get("priority", "Средний"))
    real_category = await match_select(db_id, "Категория", format_option(data.get("category", "💳 Прочее")))
    user_notion_id = data.get("user_notion_id", "")

    props = {
        "Задача":    _title(data["title"]),
        "Статус":    {"status": {"name": "Not started"}},
        "Приоритет": _select(real_priority),
        "Категория": _select(real_category),
    }
    if data.get("deadline"):
        props["Дедлайн"] = _date(data["deadline"])
    if data.get("reminder_time"):
        props["Напоминание"] = _date(data["reminder_time"])
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)

    result = await page_create(db_id, props)
    if not result:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    # Запоминаем последнюю созданную запись для контекстного редактирования
    last_record_set(uid, "task", result)

    # Сохраняем поля повторения если задача повторяющаяся
    _repeat = data.get("repeat") or "Нет"
    if _repeat and _repeat != "Нет":
        from core.notion_client import update_task_repeat_fields
        await update_task_repeat_fields(result, _repeat, data.get("day_of_week"), data.get("repeat_time"))

    # Планируем напоминание и дедлайн
    cid = chat_id or message.chat.id
    tz_offset = await _get_user_tz(uid)
    
    if data.get("reminder_time"):
        await _schedule_reminder(cid, data["title"], data["reminder_time"], result, tz_offset)
    
    if data.get("deadline"):
        deadline = data["deadline"]
        if "T" not in deadline:
            deadline = deadline + "T09:00"
        await _schedule_deadline_check(cid, data["title"], deadline, result, tz_offset)

    extra = ""
    if data.get("for_practice") and config.arcana.db_tasks:
        real_priority = await match_select(config.arcana.db_tasks, "Приоритет", data.get("priority", "Средний"))
        real_category = await match_select(config.arcana.db_tasks, "Категория", data.get("category", "💳 Прочее"))
        
        arcana_props = {
            "Задача":    _title(data["title"]),
            "Статус":    {"status": {"name": "Not started"}},
            "Приоритет": _select(real_priority),
            "Категория": _select(real_category),
        }
        if data.get("deadline"):
            arcana_props["Дедлайн"] = _date(data["deadline"])
        
        arcana_result = await page_create(config.arcana.db_tasks, arcana_props)
        if arcana_result:
            extra = "\n🔮 Также добавлено в задачи Arcana"

    deadline_display = (data.get("deadline") or "без даты").replace("T", " ")
    reminder_display = (data.get("reminder_time") or "").replace("T", " ")

    repeat_line = ""
    _repeat = data.get("repeat") or "Нет"
    if _repeat and _repeat != "Нет":
        repeat_parts = [_repeat]
        _dow = data.get("day_of_week") or ""
        _rtime = data.get("repeat_time") or ""
        if _dow:
            repeat_parts.append(_dow)
        if _rtime:
            repeat_parts.append(f"в {_rtime}")
        repeat_line = f"\n🔄 Повтор: {' '.join(repeat_parts)}"

    msg_id = data.get("msg_id")
    text_content = (
        f"✅ <b>Задача создана!</b>\n"
        f"📌 {data['title']}\n"
        f"🏷 {real_category} · {real_priority}\n"
        f"📅 Дедлайн: {deadline_display}\n"
        f"🔔 Напоминание: {reminder_display}{repeat_line}{extra}"
    )
    
    # Редактируем старое сообщение вместо создания нового
    if msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text_content,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("edit_message error: %s, fallback to answer", e)
            await message.answer(text_content, parse_mode="HTML")
    else:
        await message.answer(text_content, parse_mode="HTML")

    try:
        from nexus.handlers.memory import suggest_memory
        title    = data.get("title", "")
        category = data.get("category", "")
        # Убираем "купить/купи" из начала, оставляем только объект
        import re as _re
        item = _re.sub(r"^\s*(купить|купи)\s+", "", title, flags=_re.IGNORECASE).strip() or title
        # Имя категории без эмодзи ("🐾 Коты" → "Коты")
        cat_name = _re.sub(r"^[\s\U00010000-\U0010ffff\u2600-\u27ff\u2300-\u23ff]+", "", category).strip()
        suggest_text = f"{item} ({cat_name})" if cat_name else item
        if suggest_text:
            await suggest_memory(message, suggest_text, data.get("user_notion_id", ""))
    except Exception as e:
        logger.debug("auto_suggest skip: %s", e)

# ── Task done (fuzzy) ──────────────────────────────────────────────────────────

_DONE_STOP_WORDS = {
    "сделала", "сделал", "сделано", "выполнила", "выполнил", "выполнено",
    "закончила", "закончил", "завершила", "завершил", "готово", "готова",
    "позвонила", "позвонил", "написала", "написал", "отправила", "отправил",
    "забрала", "забрал", "купила", "купил", "отметь", "выполненным",
    "уже", "я", "и", "в", "на", "к", "за",
}


def _hint_words(text: str):
    """Извлечь значимые слова из фразы (без стоп-слов и коротких)."""
    result = set()
    for w in text.lower().split():
        w_clean = w.strip(".,!?;:—–\"'")
        if w_clean and w_clean not in _DONE_STOP_WORDS and len(w_clean) > 2:
            result.add(w_clean)
    return result


def _task_score(task_title: str, hint_words) -> int:
    """Сколько слов из hint_words есть в title задачи."""
    if not hint_words:
        return 0
    title_low = task_title.lower()
    return sum(1 for w in hint_words if w in title_low)


async def handle_task_done(message: Message, task_hint: str, user_notion_id: str = "") -> None:
    """Найти активную задачу по ключевым словам и отметить выполненной."""
    import random
    from core.notion_client import update_task_status

    uid = message.from_user.id
    hint_words = _hint_words(task_hint)
    if not hint_words:
        await message.answer("⚠️ Не понял о какой задаче речь. Напиши точнее.")
        return

    tasks = await tasks_active(user_notion_id=user_notion_id)
    if not tasks:
        await message.answer("📭 Нет активных задач.")
        return

    # Оценить каждую задачу (сохраняем props для проверки повтора)
    scored = []
    for t in tasks:
        title_parts = t["properties"].get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else ""
        if not title:
            continue
        score = _task_score(title, hint_words)
        if score > 0:
            scored.append((score, title, t["id"], t["properties"]))

    if not scored:
        await message.answer(
            f"🔍 Не нашла задачу по: «{task_hint[:60]}»\n"
            f"Проверь активные задачи: /tasks"
        )
        return

    scored.sort(key=lambda x: x[0], reverse=True)

    # Единственный хороший матч — отметить сразу
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        _, title, task_id, task_props = scored[0]
        repeat = (task_props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
        if repeat and repeat != "Нет":
            await _handle_recurring_task_reset(message, task_id, task_props, repeat, title, uid)
            return
        result = await update_task_status(task_id, "Done")
        if result:
            phrase = random.choice(_DONE_PHRASES)
            await message.answer(f"{phrase}\n✅ {title} — выполнено")
        else:
            await message.answer("⚠️ Ошибка обновления в Notion.")
        return

    # Несколько одинаковых матчей — показать кнопки выбора (до 5)
    top = scored[:5]
    buttons = []
    for _, title, task_id, _ in top:
        short_title = title[:32] + ("…" if len(title) > 32 else "")
        buttons.append([InlineKeyboardButton(
            text=f"✅ {short_title}",
            callback_data=f"task_done_select_{task_id}"
        )])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel")])
    await message.answer(
        "🔍 Нашла несколько подходящих задач. Какую отметить выполненной?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data.startswith("task_done_select_"))
async def task_done_select(call: CallbackQuery) -> None:
    logger.info("task_done_select callback: data=%s uid=%s", call.data, call.from_user.id)
    import random
    from core.notion_client import update_task_status
    task_id = call.data[len("task_done_select_"):]
    uid = call.from_user.id
    logger.info("task_done_select: task_id=%s", task_id)

    # Получить props задачи чтобы проверить повтор
    repeat = "Нет"
    task_props = {}
    title_text = ""
    try:
        client = get_notion()
        page = await client.pages.retrieve(page_id=task_id)
        task_props = page.get("properties", {})
        repeat = (task_props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
        title_parts = task_props.get("Задача", {}).get("title", [])
        title_text = title_parts[0]["plain_text"] if title_parts else ""
    except Exception as e:
        logger.warning("task_done_select: не удалось получить props: %s", e)

    await call.message.edit_reply_markup()

    if repeat and repeat != "Нет":
        await call.answer("🔄 Сброс повтора")
        await _handle_recurring_task_reset(call.message, task_id, task_props, repeat, title_text, uid)
        return

    result = await update_task_status(task_id, "Done")
    if result:
        phrase = random.choice(_DONE_PHRASES)
        await call.answer("✅ Записано!")
        await call.message.reply(phrase + "\n✅ Выполнено")
    else:
        await call.answer("⚠️ Ошибка обновления", show_alert=True)


# ── Edit record (fuzzy) ────────────────────────────────────────────────────────

async def handle_edit_record(
    message: Message,
    record_hint: str,
    field: str = "",
    new_value: str = "",
    edits: list | None = None,
    record_type: str = "task",
    user_notion_id: str = "",
) -> None:
    """Найти запись по ключевым словам (или последнюю) и обновить поле(я)."""
    from core.notion_client import match_select, update_page
    from core.config import config

    uid = message.from_user.id

    # Нормализуем к списку edits
    field_map = {
        "name": "title", "имя": "title", "название": "title",
        "категория": "category", "категорию": "category",
        "приоритет": "priority", "дедлайн": "deadline",
        "источник": "source",
    }
    if not edits:
        if field and new_value:
            edits = [{"field": field, "new_value": new_value}]
        else:
            await message.answer("⚠️ Не понял что и на что менять. Уточни:\n"
                                 "<code>поменяй категорию [запись] на [новое значение]</code>")
            return

    # Нормализуем field-синонимы во всех edits
    edits = [{"field": field_map.get(e["field"].lower(), e["field"].lower()), "new_value": e["new_value"]} for e in edits]

    hint_words = _hint_words(record_hint)

    # Пустой hint → берём последнюю запись пользователя из SQLite
    if not hint_words:
        last = last_record_get(uid)
        if not last:
            await message.answer("⚠️ Не понял какую запись изменить. Напиши точнее, например:\n"
                                 "<code>поменяй категорию [название] на [новое]</code>")
            return
        db_type_last, page_id_last = last
        # Определяем тип записи из контекста если не задан явно
        if record_type == "task" and db_type_last == "finance":
            record_type = "finance"
        for edit in edits:
            await _apply_edit(message, record_type, page_id_last, None, edit["field"], edit["new_value"],
                              user_notion_id=user_notion_id, from_context=True)
        return

    # Поиск задачи по hint_words
    tasks = await tasks_active(user_notion_id=user_notion_id)
    scored = []
    for t in tasks:
        title_parts = t["properties"].get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else ""
        if not title:
            continue
        score = _task_score(title, hint_words)
        if score > 0:
            scored.append((score, title, t["id"]))

    if not scored:
        await message.answer(f"🔍 Не нашла задачу по: «{record_hint[:60]}»")
        return

    scored.sort(key=lambda x: x[0], reverse=True)
    _, title, task_id = scored[0]
    for edit in edits:
        await _apply_edit(message, "task", task_id, title, edit["field"], edit["new_value"],
                          user_notion_id=user_notion_id)


async def _apply_edit(
    message: Message,
    record_type: str,
    page_id: str,
    title: Optional[str],
    field: str,
    new_value: str,
    user_notion_id: str = "",
    from_context: bool = False,
) -> None:
    """Применить правку к Notion-странице (задача или финансы)."""
    from core.notion_client import match_select, update_page, _title as _t, _select as _s, _date as _d
    from core.config import config

    ctx_label = " (последняя запись)" if from_context else ""

    try:
        if record_type == "finance":
            db_id = config.nexus.db_finance
            if field == "category":
                real_cat = await match_select(db_id, "Категория", new_value)
                await update_page(page_id, {"Категория": _s(real_cat)})
                label = title or "последняя запись"
                await message.answer(f"✏️ Категория{ctx_label}:\n🏷 → {real_cat}")
            elif field == "source":
                real_src = await match_select(db_id, "Источник", new_value)
                await update_page(page_id, {"Источник": _s(real_src)})
                await message.answer(f"✏️ Источник{ctx_label}:\n💳 → {real_src}")
            else:
                await message.answer(f"⚠️ Для финансов могу менять: категорию, источник.")
            return

        # Задача
        db_id = config.nexus.db_tasks
        label = title or "последняя задача"
        if field == "title":
            await update_page(page_id, {"Задача": _t(new_value)})
            await message.answer(f"✏️ Переименовано{ctx_label}:\n«{label}» → «{new_value}»")
        elif field == "category":
            real_cat = await match_select(db_id, "Категория", new_value)
            await update_page(page_id, {"Категория": _s(real_cat)})
            await message.answer(f"✏️ Категория{ctx_label}:\n📌 {label}\n🏷 → {real_cat}")
        elif field == "priority":
            real_pr = await match_select(db_id, "Приоритет", new_value)
            await update_page(page_id, {"Приоритет": _s(real_pr)})
            await message.answer(f"✏️ Приоритет{ctx_label}:\n📌 {label}\n⚡ → {real_pr}")
        elif field == "deadline":
            await update_page(page_id, {"Дедлайн": _d(new_value)})
            await message.answer(f"✏️ Дедлайн{ctx_label}:\n📌 {label}\n📅 → {new_value}")
        else:
            await message.answer(f"⚠️ Не знаю поле «{field}». Могу менять: категорию, приоритет, название, дедлайн.")
    except Exception as e:
        logger.error("_apply_edit error: %s", e)
        await message.answer("⚠️ Ошибка при обновлении.")


async def handle_tasks_today(message: Message, user_notion_id: str = "") -> None:
    tasks = await tasks_active(user_notion_id=user_notion_id)
    if not tasks:
        await message.answer("📭 Активных задач нет.")
        return

    icons = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "⚪"}
    lines = []
    for t in tasks:
        props = t["properties"]
        title_parts = props.get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "Низкий")
        deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")[:10]
        lines.append(f"{icons.get(priority, '⚪')} {title}{(' · ' + deadline) if deadline else ''}")

    await message.answer("📋 <b>Активные задачи:</b>\n\n" + "\n".join(lines))
