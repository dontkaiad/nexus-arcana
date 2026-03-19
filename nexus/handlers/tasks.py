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

async def _update_user_tz(message: Message, text: str) -> None:
    from core.notion_client import memory_set
    uid = message.from_user.id
    
    system = """Пользователь указывает часовой пояс. Ответь ТОЛЬКО числом — смещение UTC в часах.
Примеры: Екатеринбург=5, Москва=3, Дубай=4, Берлин=1, Бангкок=7, Токио=9, Новосибирск=7, Иркутск=8
Если не понял → 3"""
    
    try:
        raw = await ask_claude(text, system=system, max_tokens=5, model="claude-haiku-4-5-20251001")
        offset = int(raw.strip().split()[0])
    except Exception:
        offset = 3
    
    _user_tz_offset[uid] = offset
    await memory_set(f"tz_{uid}", str(offset), "Настройки")
    sign = "+" if offset >= 0 else ""
    await message.answer(f"🌍 Запомнила: ты в UTC{sign}{offset}. Все расчёты будут по твоему времени!")

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

    # Если в сообщении есть "напомни" + в data уже есть deadline (как reminder_time из deadline)
    # → reminder_time = deadline из data, спрашиваем дедлайн
    has_remind = _has_remind_word(original_text)

    if has_remind and data.get("deadline"):
        # "напомни завтра в 11" → deadline = reminder_time, спросить дедлайн
        data["reminder_time"] = data["deadline"]
        data["deadline"] = None

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
        now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")
        deadline_str = pending.get("deadline") or "не указана"
        
        now_dt = datetime.now(timezone(timedelta(hours=tz_offset)))
        is_night = now_dt.hour < 5
        tomorrow_note = "ВАЖНО: сейчас ночь (до 05:00) — 'завтра' означает СЕГОДНЯ (тот же календарный день)!" if is_night else ""

        system = f"""Пользователь указывает когда напомнить. Парсь и верни ТОЛЬКО JSON без markdown:
{{"reminder_time": "YYYY-MM-DDTHH:MM или null"}}

Правила:
- "через 2 мин" → через 2 минуты от сейчас
- "в 10:00" → в 10:00 (если прошло то завтра)
- "завтра в 15:00" → завтра в 15:00
- "через час" → через час от сейчас
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
        now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")
        
        system = f"""Пользователь указывает новое напоминание. Парсь и верни ТОЛЬКО JSON без markdown:
{{"reminder_time": "YYYY-MM-DDTHH:MM"}}

Правила:
- "через 2 часа" → через 2 часа от сейчас
- "завтра в 10:00" → завтра в 10:00
- "в понедельник" → в понедельник в 09:00

Сейчас: {now_str} (МСК, UTC+{tz_offset})"""
        
        text = maybe_convert(message.text)
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
        props["Пользователь"] = _relation(user_notion_id)

    result = await page_create(db_id, props)
    if not result:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

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
