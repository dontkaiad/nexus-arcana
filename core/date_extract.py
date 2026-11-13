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


# ── parse_when: дата и время из сырой фразы, раздельно (без LLM) ───────────────
# LLM отдаёт только сырую фразу; здесь она превращается в {date, time}.
# Месяцы — ТОЛЬКО точные формы (без нечёткого поиска): «ноября» да, «ночября» нет.

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
_MONTH_ALT = "|".join(MONTHS)

_WHEN_REL_RE = REL_TIME_RE
_WHEN_MONTH_DATE_RE = _re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\b", _re.IGNORECASE)
_WHEN_DAY_NUM_RE = _re.compile(r"\b(\d{1,2})(?:\s*-?\s*(?:го|е|ое))?\s+числ\w*", _re.IGNORECASE)
_WHEN_HHMM_RE = _re.compile(r"(?<![\d:])(?:в\s+)?([01]?\d|2[0-3]):([0-5]\d)(?![\d:])", _re.IGNORECASE)
_WHEN_HOURS_RE = _re.compile(r"(?<![\d:])(?:(?:в|на)\s+)?([01]?\d|2[0-3])\s*час(?:а|ов)?\b", _re.IGNORECASE)
_WHEN_AT_HOUR_RE = _re.compile(
    rf"\bв\s+([01]?\d|2[0-3])\b(?![\s]*(?:[:.\d]|{_MONTH_ALT}|числ))", _re.IGNORECASE
)
_WHEN_DAYWORD_RE = _re.compile(r"\b(сегодня|завтра|послезавтра)\b", _re.IGNORECASE)
_WHEN_WEEKDAY_RE = _re.compile(
    r"(?:\b(?:в|во|на|до|к)\s+)?\b(" + "|".join(sorted(WEEKDAY_MAP, key=len, reverse=True)) + r")\b",
    _re.IGNORECASE,
)


def _days_in_month(y: int, m: int) -> int:
    nxt = datetime(y + (m == 12), (m % 12) + 1, 1)
    return (nxt - timedelta(days=1)).day


def _day_of_month_to_date(day: int, now: datetime) -> Optional[str]:
    """«20 числа»: день ≥ сегодняшнего → текущий месяц, иначе следующий
    (первый месяц, где такой день существует)."""
    y, m = now.year, now.month
    if day < now.day:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for _ in range(12):
        if 1 <= day <= _days_in_month(y, m):
            return "%04d-%02d-%02d" % (y, m, day)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return None


def parse_when(text: str, tz_offset: int, now: Optional[datetime] = None) -> dict:
    """Сырая фраза → {"date": 'YYYY-MM-DD'|None, "time": 'HH:MM'|None}.

    Понимает: «через N мин/ч/дн», «сегодня/завтра/послезавтра» (ночью до 05:00
    голое «завтра» = сегодня), день недели, «27 ноября», «20 числа»,
    время «15:00», «в 15», «на 15 часов». Не нашла — поля None.
    Время без даты НЕ получает дату (её берёт merge_when из задачи).
    """
    out = {"date": None, "time": None}
    if not text:
        return out
    if now is None:
        now = datetime.now(timezone(timedelta(hours=tz_offset)))
    low = text.lower()

    rm = REL_TIME_RE.search(low)
    if rm:
        n, unit = int(rm.group(1)), rm.group(2)
        if unit.startswith("мин"):
            res = now + timedelta(minutes=n)
        elif unit.startswith("ч"):
            res = now + timedelta(hours=n)
        else:
            res = now + timedelta(days=n)
        out["date"] = res.strftime("%Y-%m-%d")
        out["time"] = None if unit.startswith("д") else res.strftime("%H:%M")
        return out

    m = _WHEN_MONTH_DATE_RE.search(low)
    if m:
        day, month = int(m.group(1)), MONTHS[m.group(2)]
        year = now.year
        if 1 <= day <= _days_in_month(year, month):
            if (month, day) < (now.month, now.day):
                year += 1
            if day <= _days_in_month(year, month):
                out["date"] = "%04d-%02d-%02d" % (year, month, day)
    if out["date"] is None:
        m = _WHEN_DAY_NUM_RE.search(low)
        if m:
            out["date"] = _day_of_month_to_date(int(m.group(1)), now)
    if out["date"] is None:
        m = _WHEN_DAYWORD_RE.search(low)
        if m:
            shift = HUMAN_DATE_MAP[m.group(1)]
            if m.group(1) == "завтра" and now.hour < 5:
                shift = 0
            out["date"] = (now + timedelta(days=shift)).strftime("%Y-%m-%d")
    if out["date"] is None:
        m = _WHEN_WEEKDAY_RE.search(low)
        if m:
            diff = (WEEKDAY_MAP[m.group(1)] - now.weekday()) % 7 or 7
            out["date"] = (now + timedelta(days=diff)).strftime("%Y-%m-%d")

    for rx in (_WHEN_HHMM_RE, _WHEN_HOURS_RE, _WHEN_AT_HOUR_RE):
        tm = rx.search(low)
        if tm:
            hh = int(tm.group(1))
            mm = int(tm.group(2)) if rx is _WHEN_HHMM_RE else 0
            out["time"] = "%02d:%02d" % (hh, mm)
            break
    return out


def merge_when(when: dict, *existing_walls: str, default_time: Optional[str] = None) -> Optional[str]:
    """Слить распарсенное {date,time} с существующими значениями задачи.

    existing_walls — настенные строки 'YYYY-MM-DD[THH:MM]' по убыванию
    приоритета (напр. напоминание, затем дедлайн): дату берём из первой
    непустой, время — из первой, где оно есть. Результат: 'YYYY-MM-DDTHH:MM',
    'YYYY-MM-DD' (времени нигде нет и default_time не задан) или None
    (даты нет ни в тексте, ни в задаче).
    """
    date = when.get("date")
    time = when.get("time")
    if not date:
        for w in existing_walls:
            if w:
                date = w[:10]
                break
    if not time:
        for w in existing_walls:
            if w and "T" in w:
                time = w[11:16]
                break
    if not date:
        return None
    time = time or default_time
    return f"{date}T{time}" if time else date


_WHEN_STRIP_RES = (
    _WHEN_REL_RE, _WHEN_MONTH_DATE_RE, _WHEN_DAY_NUM_RE, _WHEN_HHMM_RE,
    _WHEN_HOURS_RE, _WHEN_AT_HOUR_RE, _WHEN_DAYWORD_RE, _WHEN_WEEKDAY_RE,
)


def strip_when_tokens(text: str) -> str:
    """Вычеркнуть из текста всё, что parse_when понимает как дату/время."""
    out = (text or "").lower()
    for rx in _WHEN_STRIP_RES:
        out = rx.sub(" ", out)
    return out


# ── Хранение времени: соглашение Nexus (настоящий UTC в PG) ─────────────────
# Запись: локальное настенное время + пояс пользователя → aware-datetime (wall_to_aware).
# Чтение: aware из PG → локальное настенное (to_local_wall / dt_to_local_wall).
# Issue #143 (Nexus) и #267 (Arcana).

def _tz(tz_offset: int) -> timezone:
    return timezone(timedelta(hours=tz_offset))


# to_local_wall живёт в core/recurrence.py (issue #143, общий код Nexus/Arcana) —
# не дублируем, реэкспортируем рядом с остальными хелперами времени.
from core.recurrence import to_local_wall  # noqa: E402,F401


def dt_to_local_wall(dt: Optional[datetime], tz_offset: int) -> str:
    """datetime из PG → локальное 'YYYY-MM-DDTHH:MM' ('' если None).
    Наивный datetime считается уже локальным (как в to_local_wall)."""
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_tz(tz_offset))
    return dt.strftime("%Y-%m-%dT%H:%M")


def wall_to_aware(iso: str, tz_offset: int) -> Optional[datetime]:
    """Локальная настенная строка ('YYYY-MM-DDTHH:MM' | 'YYYY-MM-DD' | с offset'ом)
    → aware-datetime для записи в PG (там это настоящий UTC). None, если не разобрать.
    Date-only → полночь локального дня."""
    if not iso:
        return None
    s = iso.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s) if "T" in s else datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz(tz_offset))
    return dt
