"""core/birthdays.py — «поздравить с днём рождения» день в день (Nexus cron).

Раз в сутки: для подтверждённых людей владельца с ДР сегодня создаётся задача
«🎂 Поздравить …» с дедлайном на сегодня и уходит один пуш. Получатель ОДИН —
личный аккаунт (BIRTHDAY_PUSH_TG_ID), намеренно без fan-out на второй tg_id.
Идемпотентно: задача с таким же названием и дедлайном «сегодня» = маркер «уже было».
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Dict, List, Optional

logger = logging.getLogger("core.birthdays")

TASK_PREFIX = "🎂 Поздравить"


def is_birthday_today(birthday: Optional[date], today: date) -> bool:
    """Совпадение день/месяц. 29 февраля в невисокосный год празднуем 28-го. Год рождения не важен
    (в т.ч. заглушка 1904 для «год неизвестен»)."""
    if birthday is None:
        return False
    if (birthday.month, birthday.day) == (2, 29) and not _leap(today.year):
        return (today.month, today.day) == (2, 28)
    return (birthday.month, birthday.day) == (today.month, today.day)


def _leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def due_people(people: List[Dict], today: date) -> List[Dict]:
    """Подтверждённые, не «Я», с ДР сегодня; порядок стабилен (по id)."""
    out = [p for p in people
           if p.get("status") == "confirmed" and not p.get("is_self") and is_birthday_today(p.get("birthday"), today)]
    return sorted(out, key=lambda p: p["id"])


def task_title(person: Dict) -> str:
    return f"{TASK_PREFIX} {person['name']}"


def push_text(person: Dict, today: date) -> str:
    from core.people import age
    n = age(person.get("birthday"), bool(person.get("birthday_year_known", True)), today)
    rel = f" ({escape(person['relation'])})" if person.get("relation") else ""
    years = f" — {n}" if n else ""
    return f"🎂 Сегодня день рождения: <b>{escape(person['name'])}</b>{rel}{years}\nЗадача «Поздравить» уже в списке на сегодня."


async def send_birthday_pushes(bot, today: Optional[date] = None) -> int:
    """Cron job. Возвращает число отправленных пушей."""
    from core.config import config
    from core.location import get_user_tz
    from core.repos import pg_people_repo as people_repo
    from core.user_manager import get_user
    from nexus.repos.tasks_repo import _repo as tasks_repo
    from core.props import _title, _select, _date, _status, _relation

    tg_id = config.birthday_push_tg_id
    if not tg_id:
        logger.warning("send_birthday_pushes: BIRTHDAY_PUSH_TG_ID не задан — пропуск")
        return 0
    user = await get_user(tg_id)
    user_id = (user or {}).get("user_id", "")
    if not user_id:
        logger.warning("send_birthday_pushes: tg_id=%s не найден в identity — пропуск", tg_id)
        return 0

    if today is None:
        tz = await get_user_tz(tg_id)
        today = datetime.now(timezone(timedelta(hours=tz))).date()
    iso = today.isoformat()

    people = await asyncio.to_thread(people_repo.list_people, user_id, "confirmed", None)
    due = due_people(people, today)
    if not due:
        return 0

    existing = {t.title for t in await tasks_repo.list_all(user_id) if (t.deadline or "").startswith(iso)}
    sent = 0
    for p in due:
        title = task_title(p)
        if title in existing:
            continue                                   # уже создавали сегодня (рестарт / повторный запуск)
        try:
            props = {
                "Задача": _title(title),
                "Статус": {"status": {"name": "Not started"}},
                "Приоритет": _select("🟡 Важно"),
                "Категория": _select("👥 Люди"),
                "Дедлайн": _date(iso),                 # только дата → «на весь день», день в день
                "🪪 Пользователи": _relation(user_id),
            }
            if not await tasks_repo.create(config.nexus.db_tasks, props):
                logger.error("send_birthday_pushes: задача не создалась person=%s", p["id"])
                continue
            await bot.send_message(tg_id, push_text(p, today), parse_mode="HTML")
            sent += 1
        except Exception as e:                         # один сбой не должен глушить остальных именинников
            logger.error("send_birthday_pushes: person=%s error: %s", p["id"], e)
    return sent
