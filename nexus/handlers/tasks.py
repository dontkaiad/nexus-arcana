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
from core.notion_client import tasks_active, log_error, page_create, _title, _select, _date, db_query, get_notion
from core.layout import maybe_convert

logger = logging.getLogger("nexus.tasks")
MOSCOW_TZ = timezone(timedelta(hours=3))
router = Router()

CATEGORIES = [
    "🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба",
    "💰 Зарплата", "💳 Прочее",
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

        job_id = f"rem_{chat_id}_{title[:15]}_{reminder_dt}"
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

        job_id = f"deadline_{chat_id}_{title[:15]}_{deadline_dt}"
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

    deadline_str = data.get("deadline") or "не указана"
    deadline_display = deadline_str.replace("T", " ") if deadline_str != "не указана" else deadline_str

    msg = await message.answer(
        f"📌 <b>{data.get('title')}</b>\n"
        f"🏷 {data.get('category', '?')} · {data.get('priority', 'Средний')}\n"
        f"📅 Дедлайн: {deadline_display}\n\n"
        f"<b>⏰ Когда напомнить?</b>\n"
        f"Примеры: <code>завтра в 10:00</code>, <code>в 15:00</code>, <code>через 2 часа</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
        ]])
    )

    data["msg_id"] = msg.message_id
    _pending_set(uid, data)

async def handle_task_clarification(message: Message) -> None:
    """Обработка уточнений по задаче - парсим напоминание."""
    uid = message.from_user.id
    pending = _pending_get(uid)
    if not pending:
        return
    
    text = maybe_convert(message.text.strip())

    try:
        tz_offset = await _get_user_tz(uid)

        # Быстрый парсер для "через N мин/часов/дней" — не доверяем Claude
        relative = _parse_relative_time(text, tz_offset)
        if relative:
            logger.info("handle_task_clarification: relative time parsed locally: %s", relative)
            pending["reminder_time"] = relative
            _pending_set(uid, pending)
            is_practice_cat = pending.get("category", "") in PRACTICE_CATEGORIES
            deadline_display = (pending.get("deadline") or "не указана").replace("T", " ")
            reminder_display = relative.replace("T", " ")
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
            
            is_practice_cat = pending.get("category", "") in PRACTICE_CATEGORIES
            deadline_display = (pending.get("deadline") or "не указана").replace("T", " ")
            reminder_display = pending["reminder_time"].replace("T", " ")
            
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
            
            # Редактируем существующее сообщение вместо создания нового
            msg_id = pending.get("msg_id")
            if msg_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=msg_id,
                        text=text_content,
                        parse_mode="HTML",
                        reply_markup=kb
                    )
                except Exception as e:
                    logger.warning("edit_message error: %s, fallback to answer", e)
                    await message.answer(text_content, parse_mode="HTML", reply_markup=kb)
            else:
                await message.answer(text_content, parse_mode="HTML", reply_markup=kb)
            return
        else:
            logger.warning("Claude returned no reminder_time: %s", parsed)
            await message.answer("⏰ Не понял когда напомнить. Укажи время:\n<code>завтра в 10:00</code>, <code>в 15:00</code>, <code>через 2 часа</code>")
            return

    except Exception as e:
        logger.error("parse reminder error: %s", e)
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
    _pending_set(uid, d)

    await call.message.edit_reply_markup()
    await call.answer()
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
    import random
    from core.notion_client import update_task_status
    task_id = call.data.split("_", 2)[2]

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
    uid = call.from_user.id
    task_id = call.data.split("_", 2)[2]
    
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
    uid = call.from_user.id
    task_id = call.data.split("_", 2)[2]
    
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

    db_id = config.nexus.db_tasks
    real_priority = await match_select(db_id, "Приоритет", data.get("priority", "Средний"))
    real_category = await match_select(db_id, "Категория", data.get("category", "💳 Прочее"))
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
    
    msg_id = data.get("msg_id")
    text_content = (
        f"✅ <b>Задача создана!</b>\n"
        f"📌 {data['title']}\n"
        f"🏷 {real_category} · {real_priority}\n"
        f"📅 Дедлайн: {deadline_display}\n"
        f"🔔 Напоминание: {reminder_display}{extra}"
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

    hint_words = _hint_words(task_hint)
    if not hint_words:
        await message.answer("⚠️ Не понял о какой задаче речь. Напиши точнее.")
        return

    tasks = await tasks_active(user_notion_id=user_notion_id)
    if not tasks:
        await message.answer("📭 Нет активных задач.")
        return

    # Оценить каждую задачу
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
        await message.answer(
            f"🔍 Не нашла задачу по: «{task_hint[:60]}»\n"
            f"Проверь активные задачи: /tasks"
        )
        return

    scored.sort(key=lambda x: x[0], reverse=True)

    # Единственный хороший матч — отметить сразу
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        _, title, task_id = scored[0]
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
    for _, title, task_id in top:
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
    import random
    from core.notion_client import update_task_status
    task_id = call.data[len("task_done_select_"):]
    result = await update_task_status(task_id, "Done")
    if result:
        phrase = random.choice(_DONE_PHRASES)
        await call.message.edit_reply_markup()
        await call.answer("✅ Записано!")
        await call.message.reply(phrase + "\n✅ Выполнено")
    else:
        await call.answer("⚠️ Ошибка обновления", show_alert=True)


# ── Edit record (fuzzy) ────────────────────────────────────────────────────────

async def handle_edit_record(
    message: Message,
    record_hint: str,
    field: str,
    new_value: str,
    record_type: str = "task",
    user_notion_id: str = "",
) -> None:
    """Найти запись по ключевым словам (или последнюю) и обновить поле."""
    from core.notion_client import match_select, update_page
    from core.config import config

    uid = message.from_user.id

    if not field or not new_value:
        await message.answer("⚠️ Не понял что и на что менять. Уточни:\n"
                             "<code>поменяй категорию [запись] на [новое значение]</code>")
        return

    # Нормализуем field-синонимы
    field_map = {
        "name": "title", "имя": "title", "название": "title",
        "категория": "category", "категорию": "category",
        "приоритет": "priority", "дедлайн": "deadline",
        "источник": "source",
    }
    field = field_map.get(field.lower(), field.lower())

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
        await _apply_edit(message, record_type, page_id_last, None, field, new_value,
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
    await _apply_edit(message, "task", task_id, title, field, new_value,
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
