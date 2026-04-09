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
from nexus.handlers.utils import react
from core.layout import maybe_convert

logger = logging.getLogger("nexus.tasks")
MOSCOW_TZ = timezone(timedelta(hours=3))


def _tz_suffix(tz_offset: int) -> str:
    """Format tz offset as '+03:00' / '-05:00' for ISO 8601."""
    sign = "+" if tz_offset >= 0 else "-"
    h = abs(tz_offset)
    return f"{sign}{h:02d}:00"


def _date_with_tz(iso: str, tz_offset: int) -> dict:
    """Wrap _date() but append timezone suffix for datetime strings (with 'T').

    Date-only strings ('2026-04-09') pass through unchanged.
    Datetime strings ('2026-04-09T17:00') get '+03:00' appended so Notion
    stores the correct local time instead of interpreting it as UTC.
    """
    if "T" in iso and "+" not in iso and "Z" not in iso:
        iso = iso + _tz_suffix(tz_offset)
    return _date(iso)

router = Router()

CATEGORIES = [
    "🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
    "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
    "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба",
    "💰 Зарплата", "💳 Прочее", "👥 Люди",
]
PRACTICE_CATEGORIES = {"🕯️ Расходники", "🔮 Практика"}

_PRIORITY_ICONS = {"Срочно": "🔴", "Важно": "🟡", "Можно потом": "⚪"}

# Auto-suggest memory: count title occurrences per user (uid → {normalized_title → count})
from collections import defaultdict as _defaultdict
_autosuggest_counts: dict[int, dict[str, int]] = _defaultdict(lambda: _defaultdict(int))
_AUTOSUGGEST_MIN_REPEATS = 3


def _priority_display(priority: str) -> str:
    """'Важно' → '🟡 Важно', '🟡 Важно' → '🟡 Важно'."""
    p = (priority or "Важно").strip()
    for name, icon in _PRIORITY_ICONS.items():
        if name in p:
            return f"{icon} {name}"
    return f"🟡 {p}"

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


# ── last_task: последняя созданная задача (TTL 5 мин) для уточнений ─────────────
_LAST_TASK_TTL = 300  # 5 minutes


def _ltdb() -> _sqlite3.Connection:
    con = _sqlite3.connect(_PENDING_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS last_task "
        "(uid INTEGER PRIMARY KEY, page_id TEXT, ts REAL)"
    )
    con.commit()
    return con


def _last_task_set(uid: int, page_id: str) -> None:
    with _ltdb() as con:
        con.execute(
            "INSERT OR REPLACE INTO last_task (uid, page_id, ts) VALUES (?,?,?)",
            (uid, page_id, _time.time()),
        )


def _last_task_get(uid: int) -> Optional[str]:
    """Вернуть page_id последней задачи или None если истёк TTL."""
    with _ltdb() as con:
        row = con.execute(
            "SELECT page_id, ts FROM last_task WHERE uid=?", (uid,)
        ).fetchone()
    if not row:
        return None
    if _time.time() - row[1] > _LAST_TASK_TTL:
        _last_task_del(uid)
        return None
    return row[0]


def _last_task_del(uid: int) -> None:
    with _ltdb() as con:
        con.execute("DELETE FROM last_task WHERE uid=?", (uid,))


# ── Global state ───────────────────────────────────────────────────────────────
_user_tz_offset: dict = {}
_scheduler = None
_bot: Optional[Bot] = None

# ── Multi-select state for task_done ──────────────────────────────────────────
_done_multi_tasks: dict[int, list] = {}     # uid → [(score, title, task_id, props), ...]
_done_multi_selected: dict[int, set] = {}   # uid → set of selected task_ids


def _done_multi_kb(uid: int) -> InlineKeyboardMarkup:
    tasks = _done_multi_tasks.get(uid, [])
    selected = _done_multi_selected.get(uid, set())
    buttons = []
    for _, title, task_id, _ in tasks:
        short = title[:30] + ("…" if len(title) > 30 else "")
        icon = "☑" if task_id in selected else "☐"
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {short}",
            callback_data=f"done_multi_toggle:{task_id}",
        )])
    buttons.append([
        InlineKeyboardButton(text="✅ Готово", callback_data="done_multi_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="done_multi_cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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


def _remove_task_jobs(task_id: str) -> None:
    """Remove all APScheduler jobs for a task (reminder + deadline)."""
    if not _scheduler:
        return
    for prefix in ("reminder_", "deadline_"):
        try:
            _scheduler.remove_job(f"{prefix}{task_id}")
            logger.info("removed job %s%s", prefix, task_id)
        except Exception:
            pass


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

            # ── Проход 2: одноразовые задачи с пропущенным напоминанием ─────────
            # (повтор = Нет, напоминание < now, статус не Done)
            filter2_once: dict = {"and": [
                {"property": "Статус", "status": {"does_not_equal": "Done"}},
                {"property": "Напоминание", "date": {"before": now_utc_str}},
            ]}
            if user_filter:
                filter2_once["and"].append(user_filter)

            for page in await db_query(db_id, filter_obj=filter2_once, page_size=100):
                try:
                    props = page["properties"]
                    task_id = page["id"]
                    repeat = (props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
                    title_parts = props.get("Задача", {}).get("title", [])
                    title = title_parts[0]["plain_text"] if title_parts else "Задача"
                    reminder_start = (props.get("Напоминание", {}).get("date") or {}).get("start", "")
                    if not reminder_start:
                        continue

                    if repeat == "Нет":
                        # ── Одноразовая задача — отправить "пропущено" СРАЗУ ──
                        try:
                            rem_dt = datetime.strptime(reminder_start[:16], "%Y-%m-%dT%H:%M").replace(
                                tzinfo=timezone(timedelta(hours=tz_offset))
                            )
                            missed_time = rem_dt.strftime("%d.%m в %H:%M")
                        except ValueError:
                            missed_time = reminder_start[:16]

                        try:
                            await update_page(task_id, {"Статус": _status("In progress")})
                        except Exception as e:
                            logger.warning("restore pass2: failed to set In progress for '%s': %s", title, e)
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="✅ Сделано!", callback_data=f"task_complete_{task_id}"),
                            InlineKeyboardButton(text="❌ Не сделал", callback_data=f"task_failed_{task_id}"),
                        ]])
                        try:
                            await _bot.send_message(
                                tg_id,
                                f"⏰ <b>Пропущено ({missed_time}):</b> {title}\n\nСделано?",
                                parse_mode="HTML",
                                reply_markup=kb,
                            )
                            logger.info("restore pass2: sent missed reminder '%s' (was at %s)", title, missed_time)
                            restored += 1
                        except Exception as e:
                            logger.error("restore pass2: failed to send missed '%s': %s", title, e)
                    else:
                        # ── Повторяющаяся задача — уведомить + сдвинуть ──
                        try:
                            rem_dt = datetime.strptime(reminder_start[:16], "%Y-%m-%dT%H:%M").replace(
                                tzinfo=timezone(timedelta(hours=tz_offset))
                            )
                            missed_time = rem_dt.strftime("%d.%m в %H:%M")
                        except ValueError:
                            missed_time = reminder_start[:16]

                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="✅ Сделано!", callback_data=f"task_complete_{task_id}"),
                            InlineKeyboardButton(text="❌ Не сделал", callback_data=f"task_failed_{task_id}"),
                        ]])
                        # Read interval for display
                        repeat_time_raw_pre = "".join(
                            t.get("plain_text", "") for t in
                            (props.get("Время повтора", {}).get("rich_text") or [])
                        )
                        _, ivl_days_pre = _parse_repeat_time(repeat_time_raw_pre)
                        repeat_display = _interval_label(ivl_days_pre) if ivl_days_pre > 1 else repeat

                        try:
                            await _bot.send_message(
                                tg_id,
                                f"⏰ <b>Пропущено ({missed_time}):</b> {title}\n"
                                f"🔄 Повтор: {repeat_display} — переношу.\n\nСделано?",
                                parse_mode="HTML",
                                reply_markup=kb,
                            )
                        except Exception as e:
                            logger.error("restore pass2: failed to send missed repeat '%s': %s", title, e)

                        # Сдвигаем до ближайшей будущей даты
                        repeat_time_raw = "".join(
                            t.get("plain_text", "") for t in
                            (props.get("Время повтора", {}).get("rich_text") or [])
                        )
                        _, ivl_days = _parse_repeat_time(repeat_time_raw)
                        new_reminder = reminder_start[:16]
                        for _ in range(400):
                            new_reminder = _next_cycle_date(new_reminder, repeat, tz_offset, ivl_days)
                            try:
                                nrem_dt = datetime.strptime(new_reminder[:16], "%Y-%m-%dT%H:%M").replace(
                                    tzinfo=timezone(timedelta(hours=tz_offset))
                                )
                            except ValueError:
                                break
                            if nrem_dt > now_utc:
                                break

                        deadline_start = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
                        update_props: dict = {"Напоминание": _date_with_tz(new_reminder, tz_offset), "Статус": _status("Not started")}
                        if deadline_start:
                            new_deadline = deadline_start[:16]
                            for _ in range(400):
                                new_deadline = _next_cycle_date(new_deadline, repeat, tz_offset, ivl_days)
                                try:
                                    dl_dt = datetime.strptime(new_deadline[:10], "%Y-%m-%d").replace(
                                        tzinfo=timezone(timedelta(hours=tz_offset))
                                    )
                                except ValueError:
                                    break
                                if dl_dt > now_utc:
                                    break
                            update_props["Дедлайн"] = _date_with_tz(new_deadline[:10], tz_offset)

                        await update_page(task_id, update_props)
                        await _schedule_reminder(tg_id, title, new_reminder, task_id, tz_offset)
                        logger.info("restore pass2: rescheduled '%s' repeat=%s ivl=%d next=%s deadline=%s",
                                     title, repeat, ivl_days, new_reminder, deadline_start or "none")
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
        async def send_reminder() -> None:
            # Ставим статус "In progress" при срабатывании напоминания
            try:
                await update_page(task_id, {"Статус": _status("In progress")})
            except Exception as e:
                logger.warning("send_reminder: failed to set In progress: %s", e)
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

        now = _now()
        if dt <= now:
            missed_seconds = (now - dt).total_seconds()
            if missed_seconds <= 120:
                logger.info("Reminder just passed (%ds ago), sending immediately: %s", missed_seconds, title)
                await send_reminder()
            else:
                logger.warning("Reminder in the past (%ds ago), skipping: %s", missed_seconds, reminder_dt)
            return

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

# ── Human date → ISO converter ────────────────────────────────────────────────

_HUMAN_DATE_MAP = {
    "сегодня": 0, "завтра": 1, "послезавтра": 2,
}
_WEEKDAY_MAP = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5,
    "воскресенье": 6, "воскресение": 6,
}


async def _human_date_to_iso(value: str, uid: int = 0) -> Optional[str]:
    """Конвертировать человекочитаемую дату в ISO строку.

    Обрабатывает: ISO даты (passthrough), 'завтра', 'послезавтра',
    'через N дней', дни недели, fallback через Claude Haiku.
    """
    import re as _re
    v = value.strip()

    # Уже ISO дата
    if _re.match(r"^\d{4}-\d{2}-\d{2}", v):
        return v

    tz_offset = await _get_user_tz(uid)
    now = datetime.now(timezone(timedelta(hours=tz_offset)))

    # "завтра", "послезавтра", "сегодня"
    low = v.lower().strip()
    if low in _HUMAN_DATE_MAP:
        result = now + timedelta(days=_HUMAN_DATE_MAP[low])
        return result.strftime("%Y-%m-%d")

    # "через N дней"
    m = _re.search(r"через\s+(\d+)\s*(дн[а-я]*|день|дней)", low)
    if m:
        days = int(m.group(1))
        result = now + timedelta(days=days)
        return result.strftime("%Y-%m-%d")

    # День недели: "в пятницу", "в понедельник"
    for day_name, wd in _WEEKDAY_MAP.items():
        if day_name in low:
            current_wd = now.weekday()
            diff = (wd - current_wd) % 7
            if diff == 0:
                diff = 7  # следующая неделя
            result = now + timedelta(days=diff)
            return result.strftime("%Y-%m-%d")

    # Fallback: Claude Haiku
    try:
        now_str = now.strftime("%Y-%m-%d %H:%M")
        system = f"Пользователь указывает дату. Верни ТОЛЬКО дату в формате YYYY-MM-DD. Без объяснений.\nСейчас: {now_str} (UTC+{tz_offset})"
        raw = await ask_claude(v, system=system, max_tokens=20, model="claude-haiku-4-5-20251001")
        raw = raw.strip()
        if _re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return raw
    except Exception as e:
        logger.error("_human_date_to_iso Claude fallback error: %s", e)

    return None


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


# ── Follow-up clarification after task creation ────────────────────────────────

_CLARIFY_REMINDER_RE = _re.compile(
    r"(?:напоминалк[уа]?|напомни(?:лку)?|поставь\s+напоминани[ея]|добавь\s+напоминани[ея])"
    r"\s+(?:в\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:часов?|ч\.?)?\b",
    _re.IGNORECASE,
)

_CLARIFY_DEADLINE_RE = _re.compile(
    r"^(?:дедлайн|до|срок(?:\s+до)?)\s+(.+)$",
    _re.IGNORECASE,
)

_CLARIFY_CATEGORY_RE = _re.compile(
    r"^(?:категори[яю])(?:\s*[—:\-])?\s+(.+)$",
    _re.IGNORECASE,
)

_CLARIFY_PRIORITY_RE = _re.compile(
    r"^(?:приоритет)(?:\s*[—:\-])?\s+(.+)$",
    _re.IGNORECASE,
)

# Combined: any of these → potential clarification
_CLARIFY_RE = _re.compile(
    r"(?:напоминалк[уа]?|напомни(?:лку)?|поставь\s+напоминани[ея]|добавь\s+напоминани[ея]"
    r"|дедлайн\s+\S|до\s+\S|срок\s+до"
    r"|категори[яю]\s+\S"
    r"|приоритет\s+\S)",
    _re.IGNORECASE,
)


async def handle_last_task_clarify(
    message: Message,
    text: str,
    uid: int,
    user_notion_id: str = "",
) -> bool:
    """Обработать уточнение после создания задачи (5-мин окно).

    Парсит напоминание/дедлайн/категорию/приоритет и применяет к последней задаче.
    Возвращает True если уточнение обработано.
    """
    from core.notion_client import update_page

    page_id = _last_task_get(uid)
    if not page_id:
        return False

    tz_offset = await _get_user_tz(uid)
    now = datetime.now(timezone(timedelta(hours=tz_offset)))

    update_props: dict = {}
    response_text = ""
    reschedule_reminder: Optional[tuple] = None

    # ── Напоминание ──────────────────────────────────────────────────────────────
    m_rem = _CLARIFY_REMINDER_RE.search(text)
    if m_rem:
        h = int(m_rem.group(1))
        mi = int(m_rem.group(2)) if m_rem.group(2) else 0
        run_dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if run_dt <= now:
            run_dt += timedelta(days=1)
        dt_str = run_dt.strftime("%Y-%m-%dT%H:%M")
        update_props["Напоминание"] = _date_with_tz(dt_str, tz_offset)
        reschedule_reminder = (dt_str, page_id)
        response_text = f"🔔 Напоминание: {run_dt.strftime('%d.%m в %H:%M')}"

    # ── Дедлайн ──────────────────────────────────────────────────────────────────
    elif _CLARIFY_DEADLINE_RE.search(text):
        m_dl = _CLARIFY_DEADLINE_RE.search(text)
        dl_raw = m_dl.group(1).strip()
        iso_date = await _human_date_to_iso(dl_raw, uid)
        if iso_date:
            update_props["Дедлайн"] = _date_with_tz(iso_date, tz_offset)
            response_text = f"📅 Дедлайн: {iso_date}"

    # ── Категория ─────────────────────────────────────────────────────────────────
    elif _CLARIFY_CATEGORY_RE.search(text):
        m_cat = _CLARIFY_CATEGORY_RE.search(text)
        from core.classifier import _TASK_CATS
        cat_raw = m_cat.group(1).strip()
        real_cat = cat_raw
        for tc in _TASK_CATS:
            if cat_raw.lower() in tc.lower():
                real_cat = tc
                break
        update_props["Категория"] = _select(real_cat)
        response_text = f"🏷 Категория: {real_cat}"

    # ── Приоритет ─────────────────────────────────────────────────────────────────
    elif _CLARIFY_PRIORITY_RE.search(text):
        m_pri = _CLARIFY_PRIORITY_RE.search(text)
        pri_raw = m_pri.group(1).strip().lower()
        _pri_map = {
            "срочно": "Срочно", "важно": "Важно",
            "можно потом": "Можно потом", "потом": "Можно потом",
        }
        real_pri = _pri_map.get(pri_raw, pri_raw.capitalize())
        update_props["Приоритет"] = _select(real_pri)
        response_text = f"🎯 Приоритет: {real_pri}"

    if not update_props:
        return False

    try:
        await update_page(page_id, update_props)

        if reschedule_reminder:
            dt_str, tid = reschedule_reminder
            if _scheduler:
                try:
                    _scheduler.remove_job(f"reminder_{tid}")
                except Exception:
                    pass
            # Получить название задачи для нового job
            title_str = "задача"
            try:
                client = get_notion()
                pg = await client.pages.retrieve(page_id=tid)
                tp = pg.get("properties", {}).get("Задача", {}).get("title", [])
                title_str = tp[0]["plain_text"] if tp else "задача"
            except Exception:
                pass
            await _schedule_reminder(message.chat.id, title_str, dt_str, tid, tz_offset)

        _last_task_del(uid)
        await message.answer(f"✏️ {response_text}")
        return True
    except Exception as e:
        logger.error("handle_last_task_clarify error: %s", e)
        return False


import calendar as _calendar


def _parse_repeat_time(raw: str) -> tuple:
    """Parse 'HH:MM|every_Nd' → ('HH:MM', N).  Returns ('HH:MM', 0) if no interval."""
    if not raw:
        return ("09:00", 0)
    if "|every_" in raw:
        parts = raw.split("|every_", 1)
        time_str = parts[0] or "09:00"
        m = _re.match(r"(\d+)d", parts[1])
        return (time_str, int(m.group(1)) if m else 0)
    # Haiku иногда возвращает "every_Nd" без времени — извлекаем интервал, ставим дефолт 09:00
    m = _re.match(r"every_(\d+)d$", raw)
    if m:
        return ("09:00", int(m.group(1)))
    return (raw, 0)


def _interval_label(interval_days: int) -> str:
    """Human-readable label: 'каждые 2 дня' / 'каждые 5 дней'."""
    if interval_days <= 0:
        return ""
    last = interval_days % 10
    last100 = interval_days % 100
    if last == 1 and last100 != 11:
        word = "день"
    elif 2 <= last <= 4 and not (12 <= last100 <= 14):
        word = "дня"
    else:
        word = "дней"
    return f"каждые {interval_days} {word}"


def _next_cycle_date(current_date_str: str, repeat: str, tz_offset: int = 3, interval_days: int = 0) -> str:
    """Вычислить дату следующего цикла для повторяющейся задачи.

    base = max(old_date, today) — чтобы не прыгать в прошлое если задача просрочена.
    Если входная строка содержит время (YYYY-MM-DDTHH:MM) — время сохраняется.
    Возвращает YYYY-MM-DD или YYYY-MM-DDTHH:MM.
    """
    has_time = "T" in (current_date_str or "")
    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    today = now.date()

    if current_date_str:
        try:
            old_date = datetime.strptime(current_date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            old_date = today
    else:
        old_date = today

    # Всегда считаем от сегодня или позже — не от просроченной даты
    base = max(old_date, today)

    if repeat == "Ежедневно":
        step = interval_days if interval_days > 1 else 1
        next_date = base + timedelta(days=step)
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

    # Parse interval from Время повтора
    repeat_time_raw = "".join(
        t.get("plain_text", "") for t in
        (task_props.get("Время повтора", {}).get("rich_text") or [])
    )
    _, ivl_days = _parse_repeat_time(repeat_time_raw)

    deadline_prop = task_props.get("Дедлайн", {}).get("date") or {}
    current_deadline = deadline_prop.get("start", "")

    reminder_prop = task_props.get("Напоминание", {}).get("date") or {}
    current_reminder = reminder_prop.get("start", "")

    # Если напоминание уже в будущем (сдвинуто restore_reminders_on_startup) — не двигать повторно
    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    _already_future = False
    if current_reminder and "T" in current_reminder:
        try:
            rem_dt = datetime.strptime(current_reminder[:16], "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone(timedelta(hours=tz_offset))
            )
            if rem_dt > now:
                _already_future = True
                logger.info("_handle_recurring_task_reset: reminder %s already in future, skipping advance", current_reminder)
        except ValueError:
            pass

    if _already_future:
        new_reminder = current_reminder[:16]
        new_deadline = current_deadline[:10] if current_deadline else ""
    else:
        new_deadline = _next_cycle_date(current_deadline, repeat, tz_offset, ivl_days) if current_deadline else ""
        new_reminder = _next_cycle_date(current_reminder, repeat, tz_offset, ivl_days) if current_reminder else ""

    update_props = {"Статус": _status("Not started")}
    if current_deadline and new_deadline:
        update_props["Дедлайн"] = _date_with_tz(new_deadline[:10], tz_offset)
    if current_reminder and new_reminder:
        update_props["Напоминание"] = _date_with_tz(new_reminder, tz_offset)

    try:
        await update_page(task_id, update_props)
        next_display = (new_deadline[:10] if new_deadline else new_reminder[:10] if new_reminder else "?")

        # Пересоздать scheduler jobs с новыми датами
        chat_id = message.chat.id
        if _scheduler:
            # Напоминание
            if current_reminder and new_reminder:
                try:
                    _scheduler.remove_job(f"reminder_{task_id}")
                except Exception:
                    pass
                await _schedule_reminder(chat_id, title, new_reminder, task_id, tz_offset)
            # Дедлайн
            if current_deadline and new_deadline:
                try:
                    _scheduler.remove_job(f"deadline_{task_id}")
                except Exception:
                    pass
                await _schedule_deadline_check(chat_id, title, new_deadline, task_id, tz_offset)

        await message.answer(f"🔄 Повторяющаяся задача сброшена. Следующий раз: {next_display}")
    except Exception as e:
        logger.error("_handle_recurring_task_reset error: %s", e)
        await message.answer("⚠️ Ошибка обновления повторяющейся задачи.")


async def _handle_recurring_reminder_done(
    message: Message,
    task_id: str,
    title: str,
) -> None:
    """Повторяющаяся задача: напоминание выполнено → статус 'In progress', ждём дедлайн."""
    try:
        await update_page(task_id, {"Статус": _status("In progress")})
        await message.answer(f"👍 {title} — в процессе. Жду выполнения к дедлайну.")
    except Exception as e:
        logger.error("_handle_recurring_reminder_done error: %s", e)
        await message.answer("⚠️ Ошибка обновления статуса.")


async def _handle_recurring_deadline_done(
    message: Message,
    task_id: str,
    task_props: dict,
    repeat: str,
    title: str,
    uid: int = 0,
) -> None:
    """Повторяющаяся задача: дедлайн выполнен → Done, затем сброс на следующий цикл."""
    try:
        await update_page(task_id, {"Статус": _status("Done")})
    except Exception as e:
        logger.warning("_handle_recurring_deadline_done: failed to set Done: %s", e)
    await _handle_recurring_task_reset(message, task_id, task_props, repeat, title, uid)


# ── Handlers ───────────────────────────────────────────────────────────────────

_REMIND_WORDS = {"напомни", "напоминай", "remind", "напомнить", "напомни мне", "напоминалку", "напоминание"}


def _has_remind_word(text: str) -> bool:
    """Проверить что в тексте есть слово-триггер напоминания."""
    low = text.lower()
    return any(w in low for w in _REMIND_WORDS)


_NUDGE_SYSTEM = """Ты знаешь человека с СДВГ — её зовут Кай, женский род. Обращайся к ней напрямую на «ты». Её паттерны прокрастинации:
- Откладывает задачи без чёткого дедлайна
- Откладывает административные/бюрократические дела
- Откладывает дела требующие длительной концентрации
- Откладывает неприятные но важные дела (врач, документы, звонки)
- Легко забывает задачи без напоминания
Пользователь только что создал задачу. Определи: есть ли риск прокрастинации?
Если ДА — дай ОДИН короткий, не банальный совет (1 предложение, начни с эмодзи).
Учитывай что у человека уже есть напоминания и дедлайны — не советуй их ставить.
Если риска нет (задача срочная/простая/приятная) — верни пустую строку.
Отвечай ТОЛЬКО советом или пустой строкой. Без объяснений."""


async def _check_procrastination_nudge(title: str) -> str:
    try:
        result = await ask_claude(
            title,
            system=_NUDGE_SYSTEM,
            max_tokens=100,
            model="claude-haiku-4-5-20251001",
        )
        result = result.strip()
        if not result or result.lower() in ("нет", "no", ""):
            return ""
        # Haiku иногда возвращает мета-ответы вместо пустой строки
        _skip = ("пустая строка", "пустую строку", "нет риска", "не требуется", "задача простая")
        if any(s in result.lower() for s in _skip):
            return ""
        return result
    except Exception:
        return ""


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
        repeat_time_raw = data.get("repeat_time") or "09:00"
        time_str, _ivl = _parse_repeat_time(repeat_time_raw)
        try:
            h, m = map(int, time_str.split(":"))
        except Exception:
            h, m = 9, 0
        tz_offset = await _get_user_tz(uid)
        user_tz = timezone(timedelta(hours=tz_offset))
        now = datetime.now(user_tz)
        first_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if first_run <= now:
            first_run = first_run + timedelta(days=1)
        # Повторяющаяся задача: дедлайн НЕ ставим (только reminder_time для повтора)
        # Дедлайн оставляем только если пользователь задал его явно (напр. "до пт")
        if not data.get("deadline"):
            data["deadline"] = None
        data["reminder_time"] = first_run.strftime("%Y-%m-%dT%H:%M")
        logger.info("handle_task_parsed: repeat=%s ivl=%d → first_run=%s deadline=%s repeat_time_raw=%s",
                     repeat, _ivl, first_run, data["deadline"], repeat_time_raw)
        await _do_save_task(message, data, chat_id=message.chat.id, uid=uid)
        return

    # Определяем оригинальный текст из message
    original_text = message.text or ""

    # Если Haiku вернул отдельный reminder (отличается от deadline) — используем его напрямую
    if data.get("reminder") and data["reminder"] != data.get("deadline"):
        data["reminder_time"] = data.pop("reminder")
        logger.info("handle_task_parsed: explicit reminder from Haiku: %s", data["reminder_time"])
        await _do_save_task(message, data, chat_id=message.chat.id, uid=uid)
        return

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
                    f"🏷 {data.get('category', '?')} · {_priority_display(data.get('priority'))}\n"
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
    # Но если пользователь явно написал "дедлайн" — не обнулять его
    _has_explicit_deadline = bool(_re.search(r"\bдедлайн\b|\bдо\s+\d", original_text, _re.IGNORECASE))
    if has_remind and data.get("deadline"):
        data["reminder_time"] = data["deadline"]
        if not _has_explicit_deadline:
            data["deadline"] = None

        # Проверяем: пользователь РЕАЛЬНО указал время? (не Claude додумал)
        _user_specified_time = bool(_re.search(
            r"\b\d{1,2}[:.]\d{2}\b|\bв\s+\d{1,2}\b|\bутр\w*\b|\bвечер\w*\b|\bднём\b|\bночь\w*\b",
            original_text, _re.IGNORECASE
        ))
        # Если Claude добавил T09:00 но юзер не писал время → убрать время, спросить
        if "T" in data["reminder_time"] and not _user_specified_time:
            data["reminder_time"] = data["reminder_time"][:10]

        # Если только дата без времени → спрашиваем время
        if "T" not in data["reminder_time"]:
            msg_obj = await message.answer(
                f"📌 <b>{data.get('title')}</b>\n"
                f"🏷 {data.get('category', '?')} · {_priority_display(data.get('priority'))}\n\n"
                f"<b>⏰ В какое время напомнить?</b>\n"
                f"Примеры: <code>в 10:00</code>, <code>в 18:30</code>, <code>через 2 часа</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="❌ Отмена", callback_data="task_cancel"),
                ]])
            )
            data["msg_id"] = msg_obj.message_id
            _pending_set(uid, data)
            return

        # Если дедлайн уже задан явно пользователем — сохраняем сразу
        if data.get("deadline"):
            await _do_save_task(message, data, chat_id=message.chat.id, uid=uid)
            return

        reminder_display = data["reminder_time"].replace("T", " ")
        msg = await message.answer(
            f"📌 <b>{data.get('title')}</b>\n"
            f"🏷 {data.get('category', '?')} · {_priority_display(data.get('priority'))}\n"
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
                f"🏷 {data.get('category', '?')} · {_priority_display(data.get('priority'))}\n\n"
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
            f"🏷 {data.get('category', '?')} · {_priority_display(data.get('priority'))}\n"
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
        f"🗂 {data.get('category', '?')} · {_priority_display(data.get('priority'))}\n"
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
        f"🏷 {pending.get('category', '?')} · {_priority_display(pending.get('priority'))}\n"
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

    # Если нет _awaiting_* флагов → задача в режиме подтверждения ("Всё верно?")
    # Любой текст = уточнение задачи (дедлайн, напоминание, категория, приоритет)
    _is_confirm_mode = not pending.get("_awaiting_deadline") and not pending.get("_awaiting_combined")

    if _is_confirm_mode:
        await _handle_task_refinement(message, text, pending, uid)
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


async def _handle_task_refinement(message: Message, text: str, pending: dict, uid: int) -> None:
    """Уточнение задачи в режиме подтверждения — парсим любые поля через Haiku."""
    try:
        tz_offset = await _get_user_tz(uid)

        # Быстрый парсер для "через N мин/часов/дней" → treat as reminder
        relative = _parse_relative_time(text, tz_offset)
        if relative:
            pending["reminder_time"] = relative
            _pending_set(uid, pending)
            await _show_task_confirm(message, pending, uid)
            return

        from core.classifier import _TASK_CATS

        now_str = datetime.now(timezone(timedelta(hours=tz_offset))).strftime("%Y-%m-%d %H:%M")
        now_dt = datetime.now(timezone(timedelta(hours=tz_offset)))
        is_night = now_dt.hour < 5
        tomorrow_note = "ВАЖНО: сейчас ночь (до 05:00) — 'завтра' означает СЕГОДНЯ (тот же календарный день)!" if is_night else ""

        cats_str = ", ".join(_TASK_CATS)
        system = f"""Пользователь уточняет задачу. Парсь и верни ТОЛЬКО JSON без markdown.
Текущая задача: "{pending.get('title', '')}"

Верни ТОЛЬКО изменённые поля:
{{"deadline": "YYYY-MM-DD или null", "reminder_time": "YYYY-MM-DDTHH:MM или null", "category": "категория или null", "priority": "Срочно|Важно|Можно потом или null", "not_refinement": true/false}}

Правила:
- "дедлайн в воскресенье" → deadline=ближайшее вс
- "напомни в 19" / "напомни в воскресенье в 19" → reminder_time=YYYY-MM-DDTHH:MM
- "срочно" / "это срочно" → priority="Срочно"
- "важно" → priority="Важно"
- "потом" / "не срочно" → priority="Можно потом"
- "категория коты" → category=ближайшая из: {cats_str}
- Если текст НЕ похож на уточнение задачи (новая задача, вопрос, другая тема) → not_refinement=true
{tomorrow_note}
Сейчас: {now_str} (UTC+{tz_offset})"""

        raw = await ask_claude(text, system=system, max_tokens=200, model="claude-haiku-4-5-20251001")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        logger.info("_handle_task_refinement Claude returned: %s", raw)
        parsed = json.loads(raw)

        force = pending.pop("_force_refine", False)
        if force:
            _pending_set(uid, pending)

        if parsed.get("not_refinement") and not force:
            # Не уточнение — спросить пользователя
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✏️ Уточнение", callback_data="task_refine_yes"),
                InlineKeyboardButton(text="💬 Новое сообщение", callback_data="task_refine_no"),
            ]])
            await message.answer(
                "🤔 Это уточнение задачи или новое сообщение?",
                reply_markup=kb,
            )
            # Сохраняем текст в pending для обработки
            pending["_refine_text"] = text
            _pending_set(uid, pending)
            return

        updated = False
        if parsed.get("deadline"):
            pending["deadline"] = parsed["deadline"]
            updated = True
        if parsed.get("reminder_time"):
            pending["reminder_time"] = parsed["reminder_time"]
            updated = True
        if parsed.get("category"):
            # Найти ближайшую категорию
            raw_cat = parsed["category"]
            best = raw_cat
            for tc in _TASK_CATS:
                if raw_cat.lower() in tc.lower():
                    best = tc
                    break
            pending["category"] = best
            updated = True
        if parsed.get("priority") and parsed["priority"] in ("Срочно", "Важно", "Можно потом"):
            pending["priority"] = parsed["priority"]
            updated = True

        if updated:
            _pending_set(uid, pending)
            await _show_task_confirm(message, pending, uid)
        else:
            await message.answer("🤔 Не понял уточнение. Попробуй:\n<code>дедлайн завтра</code>, <code>напомни в 19</code>, <code>срочно</code>", parse_mode="HTML")

    except Exception as e:
        logger.error("_handle_task_refinement error: %s", e)
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
            f"🏷 {d.get('category', '?')} · {_priority_display(d.get('priority'))}\n"
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


@router.callback_query(F.data == "task_refine_yes")
async def task_refine_yes(call: CallbackQuery) -> None:
    """Пользователь подтвердил что это уточнение задачи."""
    uid = call.from_user.id
    d = _pending_get(uid)
    if not d or not d.get("_refine_text"):
        await call.answer("⏰ Сессия истекла.")
        return
    text = d.pop("_refine_text")
    d["_force_refine"] = True
    _pending_set(uid, d)
    await call.message.edit_reply_markup()
    await call.answer()
    await _handle_task_refinement(call.message, text, d, uid)


@router.callback_query(F.data == "task_refine_no")
async def task_refine_no(call: CallbackQuery) -> None:
    """Пользователь сказал что это новое сообщение — сбросить pending и обработать."""
    uid = call.from_user.id
    d = _pending_get(uid)
    text = d.get("_refine_text", "") if d else ""
    _pending_del(uid)
    await call.message.edit_reply_markup()
    await call.answer()
    if text:
        from nexus.nexus_bot import process_text
        await process_text(call.message, text)


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

@router.callback_query(F.data == "task_ok")
async def task_ok_cb(call: CallbackQuery) -> None:
    """Кнопка «👌 Ок» — просто убрать клавиатуру."""
    await call.message.edit_reply_markup()
    await call.answer()


@router.callback_query(F.data.startswith("task_subtask_"))
async def task_subtask_cb(call: CallbackQuery) -> None:
    """Кнопка «📋 Разбить на подзадачи» после создания задачи."""
    from core.list_manager import pending_set as list_pending_set
    uid = call.from_user.id

    # callback_data = "task_subtask_{rel_type}_{id_prefix}"
    parts = call.data.split("_", 3)  # ['task', 'subtask', 'work|task', id_prefix]
    rel_type = parts[2] if len(parts) > 2 else "task"
    id_prefix = parts[3] if len(parts) > 3 else ""

    # Получаем название задачи из текста сообщения (строка с 📌)
    task_name = "Подзадачи"
    if call.message and call.message.text:
        for line in call.message.text.split("\n"):
            if line.startswith("📌"):
                task_name = line.replace("📌", "").strip()
                break

    # Ищем полный task_id по префиксу через Notion
    task_id = id_prefix
    try:
        from core.config import config
        from core.notion_client import db_query
        db_id = config.arcana.db_works if rel_type == "work" else config.nexus.db_tasks
        if db_id and id_prefix:
            pages = await db_query(db_id, page_size=20)
            for page in pages:
                if page.get("id", "").replace("-", "").startswith(id_prefix.replace("-", "")):
                    task_id = page["id"]
                    break
    except Exception as e:
        logger.warning("task_subtask: lookup error: %s", e)

    list_pending_set(uid, {
        "action": "subtask_items",
        "task_id": task_id,
        "task_name": task_name,
        "rel_type": rel_type,
        "user_notion_id": "",
    })

    await call.message.edit_reply_markup()
    await call.message.answer(
        f"📋 Разбиваю «{task_name}» на подзадачи\n"
        f"Напиши пункты (каждый с новой строки или через запятую):",
        parse_mode="HTML",
    )
    await call.answer()


async def _update_streak_line(uid: int) -> str:
    """Update SQLite streak and return a formatted line (or empty string on error)."""
    try:
        from nexus.handlers.streaks import update_streak, format_streak_msg
        tz = await _get_user_tz(uid) or 3
        streak_data = await update_streak(uid, tz)
        if streak_data:
            return "\n" + format_streak_msg(
                streak_data["streak"], streak_data["best"], streak_data.get("is_new_best", False))
    except Exception as e:
        logger.debug("streak update error: %s", e)
    return ""


_DONE_PHRASES = [
    "🎉 Кай, ты просто огонь!",
    "✨ Ты просто магия!",
    "🔥 Красавица, сделала!",
    "💅 Готово, как всегда на высоте",
    "⚡ Кай справилась!",
    "🌟 Вот это продуктивность!",
]

_SUPPORT_NOT_DONE = [
    "Ничего страшного, бывает! 💛",
    "Не корю — завтра новый день 🌅",
    "Окей, отложим. Ты всё равно молодец что трекаешь 💪",
    "Без давления — перенесём? 🤗",
    "СДВГ-мозг такой, это нормально 🧠💜",
    "Главное что помнишь о задаче! 🌟",
]

_SUPPORT_POSTPONE = [
    "Разумно! Лучше перенести чем забить 📌",
    "Ок, сдвигаем. Главное — не потерять 👌",
    "Перенесено! Напомню, не переживай 🔔",
    "Иногда отложить = правильное решение ✨",
]


@router.callback_query(F.data.startswith("task_complete_"))
async def task_complete(call: CallbackQuery) -> None:
    logger.info("task_complete callback: data=%s uid=%s", call.data, call.from_user.id)
    import random
    from core.notion_client import update_task_status
    task_id = call.data.split("_", 2)[2]
    uid = call.from_user.id
    logger.info("task_complete: task_id=%s", task_id)

    # Проверяем повторяющаяся ли задача
    try:
        client = get_notion()
        page = await client.pages.retrieve(page_id=task_id)
        task_props = page.get("properties", {})
        repeat = (task_props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
        title_parts = task_props.get("Задача", {}).get("title", [])
        task_title = title_parts[0]["plain_text"] if title_parts else ""
    except Exception as e:
        logger.error("task_complete: failed to fetch task props: %s", e)
        repeat = "Нет"
        task_props = {}
        task_title = ""

    # Fallback: название из текста сообщения
    if not task_title:
        msg_text = call.message.text or ""
        if "Напоминание:" in msg_text:
            task_title = msg_text.split("Напоминание:")[1].strip().split("\n")[0].strip()
        elif "Дедлайн:" in msg_text:
            task_title = msg_text.split("Дедлайн:")[1].strip().split(".")[0].strip()

    await call.message.edit_reply_markup()

    if repeat and repeat != "Нет":
        # Повторяющаяся задача: различаем напоминание и дедлайн
        msg_text = call.message.text or ""
        is_deadline = "Дедлайн:" in msg_text
        has_deadline = bool((task_props.get("Дедлайн", {}).get("date") or {}).get("start"))
        if is_deadline or not has_deadline:
            # Дедлайн-сообщение ИЛИ задача без дедлайна (напр. interval every_Nd) → Done + reschedule
            await _handle_recurring_deadline_done(call.message, task_id, task_props, repeat, task_title, uid)
            await call.answer("✅ Выполнено!")
        else:
            await _handle_recurring_reminder_done(call.message, task_id, task_title)
            await call.answer("👍 В процессе")
    else:
        result = await update_task_status(task_id, "Done")
        if result:
            _remove_task_jobs(task_id)
            phrase = random.choice(_DONE_PHRASES)
            title_line = f"\n✅ {task_title} — выполнено" if task_title else "\n✅ Выполнено"
            await call.answer("✅ Записано!")
            await react(call, "🔥")
            streak_line = await _update_streak_line(uid)
            await call.message.reply(f"{phrase}{title_line}{streak_line}")
        else:
            await call.answer("⚠️ Ошибка обновления", show_alert=True)

@router.callback_query(F.data.startswith("task_failed_"))
async def task_failed(call: CallbackQuery) -> None:
    logger.info("task_failed callback: data=%s uid=%s", call.data, call.from_user.id)
    import random
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
    support = random.choice(_SUPPORT_NOT_DONE)
    await call.message.answer(
        f"{support}\n\n"
        "⏰ <b>Когда напомнить снова?</b>\n"
        "Примеры: <code>завтра в 10:00</code>, <code>через 2 часа</code>, <code>в понедельник</code>"
    )

@router.callback_query(F.data.startswith("task_reschedule_"))
async def task_reschedule(call: CallbackQuery) -> None:
    logger.info("task_reschedule callback: data=%s uid=%s", call.data, call.from_user.id)
    import random
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
    support = random.choice(_SUPPORT_POSTPONE)
    await call.message.answer(
        f"{support}\n\n"
        "⏰ <b>Когда напомнить снова?</b>\n"
        "Примеры: <code>завтра в 10:00</code>, <code>через 2 часа</code>, <code>в понедельник</code>"
    )

async def _update_notion_on_reschedule(task_id: str, new_reminder: str, tz_offset: int) -> None:
    """Update Notion reminder (and deadline if needed) when user reschedules.

    If the new reminder date is after the current deadline, push
    the deadline forward to match (otherwise Notion shows overdue).
    """
    from core.notion_client import get_page
    try:
        page = await get_page(task_id)
        if not page:
            logger.warning("_update_notion_on_reschedule: page not found %s", task_id[:8])
            return

        props = page.get("properties", {})
        update: dict = {"Напоминание": _date_with_tz(new_reminder, tz_offset)}

        # Если новое напоминание позже текущего дедлайна — сдвигаем дедлайн
        deadline_start = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
        if deadline_start:
            new_rem_date = new_reminder[:10]
            old_dl_date = deadline_start[:10]
            if new_rem_date > old_dl_date:
                # Сдвигаем дедлайн на дату нового напоминания
                new_dl = new_rem_date
                if "T" in new_reminder:
                    new_dl = new_reminder  # сохраняем время если есть
                update["Дедлайн"] = _date_with_tz(new_dl, tz_offset)
                logger.info("_update_notion_on_reschedule: deadline moved %s → %s", old_dl_date, new_dl)

        await update_page(task_id, update)
        logger.info("_update_notion_on_reschedule: updated task %s reminder=%s", task_id[:8], new_reminder)
    except Exception as e:
        logger.error("_update_notion_on_reschedule error: %s", e)


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
            await _update_notion_on_reschedule(task_id, relative, tz_offset)
            _pending_del(uid)
            await react(message, "⚡")
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
            await _update_notion_on_reschedule(task_id, reminder_time, tz_offset)
            _pending_del(uid)
            await react(message, "⚡")
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
    tz_offset = await _get_user_tz(uid)
    real_priority = await match_select(db_id, "Приоритет", data.get("priority") or "Важно")
    real_category = await match_select(db_id, "Категория", format_option(data.get("category", "💳 Прочее")))
    user_notion_id = data.get("user_notion_id", "")

    props = {
        "Задача":    _title(data["title"]),
        "Статус":    {"status": {"name": "Not started"}},
        "Приоритет": _select(real_priority),
        "Категория": _select(real_category),
    }
    if data.get("deadline"):
        props["Дедлайн"] = _date_with_tz(data["deadline"], tz_offset)
    if data.get("reminder_time"):
        props["Напоминание"] = _date_with_tz(data["reminder_time"], tz_offset)
    if user_notion_id:
        props["🪪 Пользователи"] = _relation(user_notion_id)

    result = await page_create(db_id, props)
    if not result:
        await message.answer("⚠️ Ошибка записи в Notion.")
        return

    # Запоминаем последнюю созданную запись для контекстного редактирования
    last_record_set(uid, "task", result)
    _last_task_set(uid, result)  # 5-мин окно для уточнений

    # Сохраняем поля повторения если задача повторяющаяся
    _repeat = data.get("repeat") or "Нет"
    if _repeat and _repeat != "Нет":
        from core.notion_client import update_task_repeat_fields
        await update_task_repeat_fields(result, _repeat, data.get("day_of_week"), data.get("repeat_time"))

    # Планируем напоминание и дедлайн
    cid = chat_id or message.chat.id

    if data.get("reminder_time"):
        logger.info("_do_save_task: scheduling reminder task_id=%s repeat=%s reminder=%s",
                     result[:8], data.get("repeat", "Нет"), data["reminder_time"])
        await _schedule_reminder(cid, data["title"], data["reminder_time"], result, tz_offset)

    if data.get("deadline"):
        deadline = data["deadline"]
        if "T" not in deadline:
            deadline = deadline + "T09:00"
        logger.info("_do_save_task: scheduling deadline task_id=%s deadline=%s", result[:8], deadline)
        await _schedule_deadline_check(cid, data["title"], deadline, result, tz_offset)

    extra = ""
    arcana_result = None
    if data.get("for_practice") and config.arcana.db_tasks:
        real_priority = await match_select(config.arcana.db_tasks, "Приоритет", data.get("priority") or "Важно")
        real_category = await match_select(config.arcana.db_tasks, "Категория", data.get("category", "💳 Прочее"))
        
        arcana_props = {
            "Задача":    _title(data["title"]),
            "Статус":    {"status": {"name": "Not started"}},
            "Приоритет": _select(real_priority),
            "Категория": _select(real_category),
        }
        if data.get("deadline"):
            arcana_props["Дедлайн"] = _date_with_tz(data["deadline"], tz_offset)
        
        arcana_result = await page_create(config.arcana.db_tasks, arcana_props)
        if arcana_result:
            extra = "\n🔮 Также добавлено в задачи Arcana"

    deadline_display = (data.get("deadline") or "без даты").replace("T", " ")
    reminder_display = (data.get("reminder_time") or "").replace("T", " ")

    repeat_line = ""
    _repeat = data.get("repeat") or "Нет"
    if _repeat and _repeat != "Нет":
        _rtime_raw = data.get("repeat_time") or ""
        _rtime_clean, _ivl = _parse_repeat_time(_rtime_raw)
        if _ivl > 1:
            repeat_parts = [_interval_label(_ivl)]
        else:
            repeat_parts = [_repeat]
        _dow = data.get("day_of_week") or ""
        if _dow:
            repeat_parts.append(_dow)
        if _rtime_clean and _rtime_clean != "09:00":
            repeat_parts.append(f"в {_rtime_clean}")
        elif _rtime_raw and "|" not in _rtime_raw:
            repeat_parts.append(f"в {_rtime_raw}")
        repeat_line = f"\n🔄 Повтор: {' '.join(repeat_parts)}"

    msg_id = data.get("msg_id")
    text_content = (
        f"⚡ <b>Задача создана!</b>\n"
        f"📌 {data['title']}\n"
        f"🏷 {real_category} · {_priority_display(real_priority)}\n"
        f"📅 Дедлайн: {deadline_display}\n"
        f"🔔 Напоминание: {reminder_display}{repeat_line}{extra}"
    )

    # Inline-кнопки: предложить разбить на подзадачи
    _rel = "work" if (data.get("for_practice") and arcana_result) else "task"
    _tid = arcana_result if _rel == "work" else result
    _suggest_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Подзадачи", callback_data=f"task_subtask_{_rel}_{_tid[:24]}"),
        InlineKeyboardButton(text="👌 Ок", callback_data="task_ok"),
    ]])

    await react(message, "⚡")

    # Редактируем старое сообщение вместо создания нового
    if msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text_content,
                parse_mode="HTML",
                reply_markup=_suggest_kb,
            )
        except Exception as e:
            logger.warning("edit_message error: %s, fallback to answer", e)
            await message.answer(text_content, parse_mode="HTML", reply_markup=_suggest_kb)
    else:
        await message.answer(text_content, parse_mode="HTML", reply_markup=_suggest_kb)

    try:
        from nexus.handlers.memory import suggest_memory
        import re as _re
        title = data.get("title", "")
        priority = data.get("priority") or ""

        # Smart recall: ищем в памяти по объекту покупки
        _purchase_match = _re.match(
            r"^\s*(купить|купи|заказать|закажи|поесть|съесть|приготовить|покормить)\s+(.+)",
            title, _re.IGNORECASE,
        )
        _recall_shown = False
        if _purchase_match:
            from core.memory import recall_from_memory
            _obj = _purchase_match.group(2).strip()
            _fact = await recall_from_memory(_obj)
            if _fact:
                await message.answer(f"💡 <i>{_fact} — как обычно?</i>")
                _recall_shown = True

        # Auto-suggest: только после 3+ повторений И только для Срочно/Важно
        _is_high_priority = ("Срочно" in priority or "Важно" in priority)
        _is_routine = bool(_re.match(r"^\s*(купить|купи|заказать|закажи|выкинуть|убрать|погладить|помыть|постирать|протереть|вынести|выбросить|поесть|съесть|приготовить|сварить|разогреть|покормить)\s+", title, _re.IGNORECASE))
        _routine_cats = ("🍜 Продукты",)
        _is_routine = _is_routine or data.get("category", "") in _routine_cats
        _uid = message.from_user.id
        _norm_title = title.strip().lower()
        if _norm_title:
            _autosuggest_counts[_uid][_norm_title] += 1
        _repeat_count = _autosuggest_counts[_uid].get(_norm_title, 0)
        if (title and title.strip() and _is_high_priority and not _is_routine
                and not _recall_shown and _repeat_count >= _AUTOSUGGEST_MIN_REPEATS):
            await suggest_memory(message, title.strip(), data.get("user_notion_id", ""))
        nudge = await _check_procrastination_nudge(data.get("title", ""))
        if nudge:
            await message.answer(nudge)
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


_CANCEL_STOP_WORDS = {
    "отмени", "отменить", "отмена", "удали", "убери", "задачу", "задачи", "задач",
}


async def handle_task_cancel(message: Message, task_hint: str, user_notion_id: str = "") -> None:
    """Найти активную задачу по ключевым словам и отменить (статус Archived)."""
    from core.notion_client import update_page

    # Убираем стоп-слова отмены из hint
    cancel_words = set()
    for w in task_hint.lower().split():
        w_clean = w.strip(".,!?;:—–\"'")
        if w_clean and w_clean not in _CANCEL_STOP_WORDS and len(w_clean) > 2:
            cancel_words.add(w_clean)

    if not cancel_words:
        await message.answer("⚠️ Укажи какую задачу отменить. Например: «отмени задачу написать Маше»")
        return

    tasks = await tasks_active(user_notion_id=user_notion_id)
    if not tasks:
        await message.answer("📭 Нет активных задач.")
        return

    scored = []
    for t in tasks:
        title_parts = t["properties"].get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else ""
        if not title:
            continue
        score = _task_score(title, cancel_words)
        if score > 0:
            scored.append((score, title, t["id"]))

    if not scored:
        await message.answer(f"🔍 Не нашёл задачу по: «{task_hint[:60]}»\nПроверь активные: /tasks")
        return

    scored.sort(key=lambda x: x[0], reverse=True)
    _, title, task_id = scored[0]

    try:
        await update_page(task_id, {"Статус": _status("Archived")})
        # Удаляем scheduler jobs
        if _scheduler:
            for prefix in ("reminder_", "deadline_"):
                try:
                    _scheduler.remove_job(f"{prefix}{task_id}")
                    logger.info("Cancelled %s job: %s%s", prefix.rstrip("_"), prefix, task_id)
                except Exception:
                    pass
        await message.answer(f"🗑️ Задача «{title}» отменена")
    except Exception as e:
        logger.error("handle_task_cancel error: %s", e)
        await message.answer("⚠️ Ошибка при отмене задачи.")


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
            f"🔍 Не нашёл задачу по: «{task_hint[:60]}»\n"
            f"Проверь активные задачи: /tasks"
        )
        return

    scored.sort(key=lambda x: x[0], reverse=True)

    # Единственный хороший матч — отметить сразу
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        _, title, task_id, task_props = scored[0]
        repeat = (task_props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
        if repeat and repeat != "Нет":
            await _handle_recurring_deadline_done(message, task_id, task_props, repeat, title, uid)
            return
        result = await update_task_status(task_id, "Done")
        if result:
            _remove_task_jobs(task_id)
            phrase = random.choice(_DONE_PHRASES)
            streak_line = await _update_streak_line(uid)
            await message.answer(f"{phrase}\n✅ {title} — выполнено{streak_line}")
        else:
            await message.answer("⚠️ Ошибка обновления в Notion.")
        return

    # Несколько одинаковых матчей — мультиселект (до 5)
    top = scored[:5]
    _done_multi_tasks[uid] = top
    _done_multi_selected[uid] = set()
    await message.answer(
        "🔍 Нашёл несколько подходящих задач. Выбери нужные и нажми ✅ Готово:",
        reply_markup=_done_multi_kb(uid),
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
        await call.answer("✅ Выполнено!")
        await _handle_recurring_deadline_done(call.message, task_id, task_props, repeat, title_text, uid)
        return

    result = await update_task_status(task_id, "Done")
    if result:
        _remove_task_jobs(task_id)
        phrase = random.choice(_DONE_PHRASES)
        streak_line = await _update_streak_line(uid)
        await call.answer("✅ Записано!")
        await call.message.reply(phrase + "\n✅ Выполнено" + streak_line)
    else:
        await call.answer("⚠️ Ошибка обновления", show_alert=True)


@router.callback_query(F.data.startswith("done_multi_toggle:"))
async def cb_done_multi_toggle(call: CallbackQuery) -> None:
    await call.answer()
    uid = call.from_user.id
    task_id = call.data.split(":", 1)[1]
    selected = _done_multi_selected.setdefault(uid, set())
    selected.discard(task_id) if task_id in selected else selected.add(task_id)
    if not _done_multi_tasks.get(uid):
        await call.message.edit_text("⏱ Сессия истекла.")
        return
    await call.message.edit_reply_markup(reply_markup=_done_multi_kb(uid))


@router.callback_query(F.data == "done_multi_confirm")
async def cb_done_multi_confirm(call: CallbackQuery) -> None:
    import random as _random
    await call.answer()
    uid = call.from_user.id
    selected = _done_multi_selected.pop(uid, set())
    tasks = _done_multi_tasks.pop(uid, [])
    if not selected:
        await call.message.edit_text("☐ Ничего не выбрано.")
        return
    from core.notion_client import update_task_status
    done_titles = []
    for _, title, task_id, task_props in tasks:
        if task_id not in selected:
            continue
        repeat = (task_props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
        if repeat and repeat != "Нет":
            await _handle_recurring_deadline_done(call.message, task_id, task_props, repeat, title, uid)
        else:
            result = await update_task_status(task_id, "Done")
            if result:
                _remove_task_jobs(task_id)
                done_titles.append(title)
    if done_titles:
        phrase = _random.choice(_DONE_PHRASES)
        streak_line = await _update_streak_line(uid)
        lines = "\n".join(f"✅ {t}" for t in done_titles)
        await call.message.edit_text(f"{phrase}\n{lines}{streak_line}")
    else:
        await call.message.edit_text("⚠️ Ошибка обновления в Notion.")


@router.callback_query(F.data == "done_multi_cancel")
async def cb_done_multi_cancel(call: CallbackQuery) -> None:
    await call.answer("Отменено")
    uid = call.from_user.id
    _done_multi_tasks.pop(uid, None)
    _done_multi_selected.pop(uid, None)
    await call.message.edit_reply_markup()


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
        "напоминание": "reminder", "напоминалку": "reminder",
        "напомни": "reminder", "напомнить": "reminder",
        "источник": "source",
        "статус": "status", "status": "status",
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
        await message.answer(f"🔍 Не нашёл задачу по: «{record_hint[:60]}»")
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
    from core.notion_client import match_select, update_page, _title as _t, _select as _s, _date as _d, get_notion
    from core.config import config

    ctx_label = " (последняя запись)" if from_context else ""

    try:
        # Проверяем что страница не архивирована
        try:
            client = get_notion()
            page = await client.pages.retrieve(page_id=page_id)
            if page.get("archived", False):
                await message.answer("⚠️ Эта запись архивирована — редактировать нельзя.")
                return
        except Exception:
            pass  # Если не смогли проверить — пробуем редактировать
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
            # Если match_select не нашёл — попробуем _TASK_CATS
            if real_cat == new_value:
                from core.classifier import _TASK_CATS
                _nv = new_value.lower().strip()
                for tc in _TASK_CATS:
                    if _nv in tc.lower():
                        real_cat = tc
                        break
            await update_page(page_id, {"Категория": _s(real_cat)})
            await message.answer(f"✏️ Категория{ctx_label}:\n📌 {label}\n🏷 → {real_cat}")
        elif field == "priority":
            real_pr = await match_select(db_id, "Приоритет", new_value)
            await update_page(page_id, {"Приоритет": _s(real_pr)})
            await message.answer(f"✏️ Приоритет{ctx_label}:\n📌 {label}\n⚡ → {_priority_display(real_pr)}")
        elif field == "status":
            _status_map = {
                "не начато": "Not started", "not started": "Not started",
                "в процессе": "In progress", "in progress": "In progress",
                "готово": "Done", "done": "Done", "сделано": "Done",
                "архив": "Archived", "archived": "Archived", "отменено": "Archived",
            }
            real_status = _status_map.get(new_value.lower(), new_value)
            await update_page(page_id, {"Статус": _status(real_status)})
            await message.answer(f"✏️ Статус{ctx_label}:\n📌 {label}\n📊 → {real_status}")
        elif field in ("deadline", "reminder"):
            # Конвертируем человекочитаемые даты в ISO
            iso_value = await _human_date_to_iso(new_value, message.from_user.id if message.from_user else 0)
            if not iso_value:
                await message.answer(f"⚠️ Не удалось распарсить дату: «{new_value}»")
                return
            if field == "deadline":
                await update_page(page_id, {"Дедлайн": _d(iso_value)})
                await message.answer(f"✏️ Дедлайн{ctx_label}:\n📌 {label}\n📅 → {iso_value}")
            else:
                await update_page(page_id, {"Напоминание": _d(iso_value)})
                await message.answer(f"✏️ Напоминание{ctx_label}:\n📌 {label}\n🔔 → {iso_value}")
        else:
            await message.answer(f"⚠️ Не знаю поле «{field}». Могу менять: категорию, приоритет, название, дедлайн.")
    except Exception as e:
        logger.error("_apply_edit error: %s", e)
        await message.answer("⚠️ Ошибка при обновлении.")


async def handle_tasks_today(message: Message, user_notion_id: str = "") -> None:
    """Задачи на сегодня: дедлайн сегодня/просрочен, напоминание сегодня, ежедневные."""
    from core.notion_client import query_pages, _with_user_filter
    from core.config import config
    from datetime import date as _date, timedelta

    uid = message.from_user.id if message.from_user else 0
    tz_offset = await _get_user_tz(uid)
    user_tz = timezone(timedelta(hours=tz_offset))
    today_str = datetime.now(user_tz).strftime("%Y-%m-%d")

    # Все активные задачи
    base_filter = {
        "and": [
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Archived"}},
            {"property": "Статус", "status": {"does_not_equal": "Complete"}},
        ]
    }
    filters = _with_user_filter(base_filter, user_notion_id)
    all_tasks = await query_pages(
        config.nexus.db_tasks, filters=filters,
        sorts=[{"property": "Приоритет", "direction": "descending"}],
        page_size=100,
    )

    _priority_icons = {"Срочно": "🔴", "Важно": "🟡", "Можно потом": "⚪"}
    _status_icons = {"In progress": "⏳", "Not started": "❌"}
    _repeat_labels = {"Ежедневно": "ежедневно", "Еженедельно": "еженедельно", "Ежемесячно": "ежемесячно"}

    def _get_interval_days(props: dict) -> int:
        """Extract interval_days from Время повтора field."""
        rt = "".join(t.get("plain_text", "") for t in (props.get("Время повтора", {}).get("rich_text") or []))
        _, ivl = _parse_repeat_time(rt)
        return ivl

    overdue = []
    today_tasks = []
    daily = []

    for t in all_tasks:
        props = t["properties"]
        title_parts = props.get("Задача", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "—"
        priority_raw = (props.get("Приоритет", {}).get("select") or {}).get("name", "Важно")
        priority = priority_raw
        for _pk in _priority_icons:
            if _pk in priority_raw:
                priority = _pk
                break
        status = (props.get("Статус", {}).get("status") or {}).get("name", "Not started")
        category = (props.get("Категория", {}).get("select") or {}).get("name", "")
        deadline_raw = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
        reminder_raw = (props.get("Напоминание", {}).get("date") or {}).get("start", "")
        repeat = (props.get("Повтор", {}).get("select") or {}).get("name", "")
        is_repeat = repeat and repeat != "Нет"

        deadline_date = deadline_raw[:10] if deadline_raw else ""
        reminder_date = reminder_raw[:10] if reminder_raw else ""
        cat_icon = category[0] if category else "📌"
        status_icon = _status_icons.get(status, "❔")

        # Время из дедлайна или напоминания
        time_str = ""
        if "T" in reminder_raw:
            time_str = reminder_raw.split("T")[1][:5]
        elif "T" in deadline_raw:
            time_str = deadline_raw.split("T")[1][:5]

        ivl = _get_interval_days(props) if is_repeat else 0

        item = {
            "cat_icon": cat_icon,
            "title": title,
            "priority": priority,
            "pri_icon": _priority_icons.get(priority, "⚪"),
            "status_icon": status_icon,
            "time_str": time_str,
            "is_repeat": is_repeat,
            "repeat": repeat,
            "interval_days": ivl,
        }

        # Ежедневные / каждые-N-дней задачи
        if is_repeat and repeat == "Ежедневно":
            daily.append(item)
        # Просроченные
        elif deadline_date and deadline_date < today_str:
            overdue.append(item)
        # Дедлайн сегодня или напоминание сегодня
        elif deadline_date == today_str or reminder_date == today_str:
            today_tasks.append(item)

    total = len(overdue) + len(today_tasks) + len(daily)
    all_today = overdue + today_tasks + daily
    n_urgent = sum(1 for it in all_today if it["priority"] == "Срочно")
    n_important = sum(1 for it in all_today if it["priority"] == "Важно")
    n_low = sum(1 for it in all_today if it["priority"] == "Можно потом")
    n_overdue = len(overdue)

    # Время суток для совета
    hour = datetime.now(user_tz).hour
    if hour < 12:
        time_of_day = "утро"
    elif hour < 18:
        time_of_day = "день"
    else:
        time_of_day = "вечер"

    # Стрик
    streak_line = "🔥 Стрик: 0 — начни сегодня!"
    try:
        from nexus.handlers.streaks import get_streak
        streak_data = get_streak(uid)  # sync, one arg
        s = streak_data.get("streak", 0) if streak_data else 0
        if s > 0:
            fire = "🔥" * min(s, 5)
            streak_line = f"{fire} {s} дней подряд"
    except Exception as e:
        logger.warning("today streak error: %s", e)

    # Бюджет на день + лимиты на грани
    budget_line = ""
    try:
        from nexus.handlers.finance import _calc_free_remaining, _get_limits, _cat_link, build_budget_message
        import os as _os
        result = await _calc_free_remaining(user_notion_id)
        if result:
            free_left, days_rem = result
            daily_budget = free_left / max(days_rem, 1)
            budget_line = f"💰 Бюджет: <b>{daily_budget:,.0f}₽/день</b>"

            # Лимиты на грани (>50%)
            mem_db = _os.environ.get("NOTION_DB_MEMORY")
            if mem_db:
                from core.notion_client import db_query
                from core.config import config
                from core.classifier import today_moscow
                limits = await _get_limits(mem_db)
                if limits:
                    today_str_b = today_moscow()
                    month_start = today_str_b[:7] + "-01"
                    try:
                        expense_recs = await db_query(config.nexus.db_finance, filter_obj={"and": [
                            {"property": "Тип", "select": {"equals": "💸 Расход"}},
                            {"property": "Дата", "date": {"on_or_after": month_start}},
                            {"property": "Дата", "date": {"on_or_before": today_str_b}},
                        ]}, page_size=500)
                        by_cat_b: dict[str, float] = {}
                        for r in expense_recs:
                            cat_r = (r["properties"].get("Категория", {}).get("select") or {}).get("name", "")
                            amt_r = r["properties"].get("Сумма", {}).get("number") or 0
                            by_cat_b[cat_r] = by_cat_b.get(cat_r, 0) + amt_r
                    except Exception:
                        by_cat_b = {}

                    warns = []
                    for lim_key, lim_val in limits.items():
                        if lim_val <= 0:
                            continue
                        spent_b = 0.0
                        for cat_k, cat_s in by_cat_b.items():
                            cl = _cat_link(cat_k)
                            if lim_key in cl or cl in lim_key:
                                spent_b += cat_s
                        pct = int(spent_b / lim_val * 100)
                        if pct >= 50:
                            color = "🔴" if pct >= 90 else "🟡" if pct >= 70 else "🟢"
                            short = lim_key.capitalize()
                            warns.append(f"{short} {pct}% {color}")
                    if warns:
                        budget_line += "\n📊 " + " · ".join(warns[:4])
    except Exception as e:
        logger.warning("handle_tasks_today: budget calc error: %s", e)

    def _fmt(it: dict) -> str:
        line = f"  {it['pri_icon']} {it['title']} · {it['cat_icon']}"
        if it.get("time_str"):
            line += f" · {it['time_str']}"
        return line

    lines: list[str] = [f"📅 <b>Сегодня · ☀️ Nexus</b>\n"]

    if total == 0:
        lines.append("🌟 На сегодня задач нет — свободный день!")
    else:
        if overdue:
            lines.append(f"<b>🔥 ПРОСРОЧЕНО</b>")
            for it in overdue:
                lines.append(_fmt(it))

        if today_tasks:
            lines.append(f"\n<b>📅 СЕГОДНЯ</b>")
            for it in today_tasks:
                lines.append(_fmt(it))

        if daily:
            lines.append(f"\n<b>🔄 ЕЖЕДНЕВНЫЕ</b>")
            for it in daily:
                time_part = f" · {it['time_str']}" if it.get("time_str") else ""
                ivl_part = f" · {_interval_label(it['interval_days'])}" if it.get("interval_days", 0) > 1 else ""
                lines.append(f"  {it['pri_icon']} {it['title']} · {it['cat_icon']}{time_part}{ivl_part}")

    if streak_line:
        lines.append(f"\n{streak_line}")
    if budget_line:
        lines.append(budget_line)

    # СДВГ-совет от Haiku (учитывает контекст)
    try:
        # Передаём реальные названия задач с временем — до 5 штук
        _tip_items = (overdue + today_tasks + daily)[:5]
        _tip_parts = []
        for it in _tip_items:
            part = f"«{it['title']}»"
            if it.get("time_str"):
                part += f" в {it['time_str']}"
            _tip_parts.append(part)
        _tip_tasks_str = ", ".join(_tip_parts) if _tip_parts else "нет"
        prompt = (
            f"Ты — СДВГ-ассистент Кай (женский род). Обращайся на «ты». "
            f"Задачи — это ДЕЛА, которые нужно СДЕЛАТЬ. НЕ объясняй их смысл и НЕ переинтерпретируй. "
            f"'Менять лоток' = убирать наполнитель. 'Помыть обувь' = помыть обувь. Понимай буквально.\n"
            f"Сейчас {time_of_day}. Задач: {total} (срочных: {n_urgent}, просроченных: {n_overdue}). "
            f"Ближайшие задачи: {_tip_tasks_str}.\n"
            f"ЗАПРЕЩЕНО: Nexus УЖЕ является напоминалкой. НИКОГДА не советуй ставить будильник, "
            f"таймер, напоминание, записать в календарь, поставить alarm — это буквально твоя работа. "
            f"НЕ выдумывай время задач — используй ТОЛЬКО время из списка выше.\n"
            f"Дай ОДИН тёплый короткий совет — помочь НАЧАТЬ делать, а не объяснять КАК. "
            f"Примеры хорошего совета: «Просто открой шкаф — руки сами сделают остальное» / «2 минуты и готово, ты справишься». "
            f"Макс 1-2 предложения. Без пафоса."
        )
        advice = await ask_claude(prompt, max_tokens=100, model="claude-haiku-4-5-20251001")
        if advice:
            lines.append(f"\n💡 {advice.strip()}")
    except Exception as e:
        logger.warning("handle_tasks_today: ADHD tip error: %s", e)

    try:
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error("handle_tasks_today: send message error: %s", e)
        # Fallback без HTML если парсинг сломался
        try:
            from aiogram.enums import ParseMode
            plain = "\n".join(lines).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
            await message.answer(plain)
        except Exception:
            await message.answer("⚠️ Ошибка формирования дайджеста. Попробуй /tasks.")


# ── /stats — статистика задач ─────────────────────────────────────────────────

async def handle_task_stats(message: Message, user_notion_id: str = "") -> None:
    """Статистика задач: за неделю, месяц, стрики, топ категорий."""
    from core.notion_client import query_pages, _with_user_filter
    from core.config import config
    import random
    from collections import Counter

    uid = message.from_user.id if message.from_user else 0
    tz_offset = await _get_user_tz(uid)
    user_tz = timezone(timedelta(hours=tz_offset))
    now = datetime.now(user_tz)
    today = now.date()
    week_start = today - timedelta(days=today.weekday())  # Понедельник
    month_start = today.replace(day=1)

    # Все задачи пользователя (без фильтра по статусу)
    filters = _with_user_filter(None, user_notion_id)
    all_tasks = await query_pages(
        config.nexus.db_tasks, filters=filters,
        sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
        page_size=200,
    )

    done_week = 0
    done_month = 0
    cancelled_week = 0
    cancelled_month = 0
    active = 0
    done_dates: list[str] = []
    cat_done: Counter = Counter()

    for t in all_tasks:
        props = t["properties"]
        status = (props.get("Статус", {}).get("status") or {}).get("name", "Not started")
        category = (props.get("Категория", {}).get("select") or {}).get("name", "")

        # Дата завершения: "Время завершения" → last_edited_time → created_time
        completion_raw = (props.get("Время завершения", {}).get("date") or {}).get("start", "")
        if not completion_raw:
            completion_raw = t.get("last_edited_time", "")
        completion_date_str = completion_raw[:10] if completion_raw else ""

        if status in ("Done", "Complete"):
            if completion_date_str:
                done_dates.append(completion_date_str)
                try:
                    cd = datetime.strptime(completion_date_str, "%Y-%m-%d").date()
                    if cd >= week_start:
                        done_week += 1
                    if cd >= month_start:
                        done_month += 1
                        if category:
                            cat_done[category] += 1
                except ValueError:
                    done_month += 1
        elif status == "Archived":
            if completion_date_str:
                try:
                    cd = datetime.strptime(completion_date_str, "%Y-%m-%d").date()
                    if cd >= week_start:
                        cancelled_week += 1
                    if cd >= month_start:
                        cancelled_month += 1
                except ValueError:
                    cancelled_month += 1
        elif status in ("Not started", "In progress"):
            active += 1

    # Стрик — единственный источник правды: SQLite streaks
    from nexus.handlers.streaks import get_streak
    _streak_data = get_streak(uid)
    streak = _streak_data.get("streak", 0)
    best_streak = _streak_data.get("best", 0)

    # Топ-3 категории
    top_cats = cat_done.most_common(3)

    # Совет от Haiku
    advice = ""
    try:
        advice = await ask_claude(
            f"Кай (женский род, СДВГ). За неделю выполнено {done_week} задач, "
            f"стрик {streak} дней. Дай ОДИН короткий мотивирующий совет. Макс 1 предложение. "
            f"ЗАПРЕТ: не советуй ставить будильник/таймер/напоминание/календарь — бот уже это делает.",
            max_tokens=80, model="claude-haiku-4-5-20251001",
        )
    except Exception:
        pass

    # Формируем вывод
    lines = [
        f"📊 <b>Статистика задач</b>\n",
        f"<b>📅 Эта неделя:</b>",
        f"  ✅ Выполнено: {done_week}",
        f"  ❌ Отменено: {cancelled_week}",
        f"  ⏳ Активных: {active}",
        f"",
        f"<b>📅 Этот месяц:</b>",
        f"  ✅ Выполнено: {done_month}",
        f"  ❌ Отменено: {cancelled_month}",
        f"  ⏳ Активных: {active}",
        f"",
        f"<b>🔥 Стрик:</b> {streak} {'дней' if streak != 1 else 'день'} подряд",
        f"<b>🏆 Лучший стрик:</b> {best_streak} {'дней' if best_streak != 1 else 'день'}",
    ]

    if top_cats:
        lines.append(f"\n<b>📈 Топ категорий (месяц):</b>")
        for cat, count in top_cats:
            lines.append(f"  {cat} — {count}")

    if advice:
        lines.append(f"\n💡 {advice.strip()}")

    await message.answer("\n".join(lines))
