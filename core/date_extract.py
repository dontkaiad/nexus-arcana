"""core/date_extract.py — детерминированное извлечение даты/времени из текста (без LLM).

Общий код Nexus и Arcana. Перенесено из nexus/handlers/tasks.py (там остались
алиасы с прежними именами) + extract_wall_datetime для fallback-ов, когда
LLM-разбор недоступен.
"""
from __future__ import annotations

import re as _re
from datetime import datetime, timedelta, timezone
from typing import Optional

HUMAN_DATE_MAP = {
    "сегодня": 0, "завтра": 1, "послезавтра": 2,
}
WEEKDAY_MAP = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "среду": 2, "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5,
    "воскресенье": 6, "воскресение": 6,
}

REL_TIME_RE = _re.compile(
    r"через\s+(\d+)\s*(мин[а-я]*|час[а-я]*|ч\b|дн[а-я]*|день|дней)",
    _re.IGNORECASE,
)


def parse_relative_time(text: str, tz_offset: int) -> Optional[str]:
    """Парсить 'через N минут/часов/дней' → 'YYYY-MM-DDTHH:MM'.

    Возвращает строку или None если паттерн не найден.
    Не использует Claude — вычисляет offset от datetime.now().
    """
    m = REL_TIME_RE.search(text)
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


_DAY_WORD_RE = _re.compile(r"\b(сегодня|завтра|послезавтра)\b", _re.IGNORECASE)
_TIME_RE = _re.compile(r"(?<!\d)(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", _re.IGNORECASE)
_TIME_HOUR_RE = _re.compile(r"\bв\s+([01]?\d|2[0-3])\b(?!\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря))", _re.IGNORECASE)


def extract_wall_datetime(text: str, tz_offset: int) -> Optional[str]:
    """Достать дедлайн из текста без LLM: «через N …», либо день (сегодня/завтра/
    послезавтра/день недели) + необязательное время («в 19:45», «в 18»).

    Возвращает локальное настенное 'YYYY-MM-DDTHH:MM' (или 'YYYY-MM-DD', если
    времени нет), либо None, если день не найден. Время без дня не угадываем.
    """
    if not text:
        return None
    rel = parse_relative_time(text, tz_offset)
    if rel:
        return rel

    now = datetime.now(timezone(timedelta(hours=tz_offset)))
    low = text.lower()
    day = None
    m = _DAY_WORD_RE.search(low)
    if m:
        day = now + timedelta(days=HUMAN_DATE_MAP[m.group(1)])
    else:
        for name, wd in WEEKDAY_MAP.items():
            if _re.search(rf"\bв\s+{name}\b", low):
                diff = (wd - now.weekday()) % 7 or 7
                day = now + timedelta(days=diff)
                break
    if day is None:
        return None

    tm = _TIME_RE.search(low)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
    else:
        th = _TIME_HOUR_RE.search(low)
        if not th:
            return day.strftime("%Y-%m-%d")
        hh, mm = int(th.group(1)), 0
    return day.strftime("%Y-%m-%d") + f"T{hh:02d}:{mm:02d}"
