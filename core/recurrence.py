"""core/recurrence.py — общая математика повторяющихся записей.

Вынесено из ``nexus/handlers/tasks.py`` чтобы Arcana (повторяющиеся Работы,
ADR-0023 раздел «repeat») использовала ту же логику вычисления следующего
цикла и разбора «Времени повтора» без дублирования. Nexus tasks.py импортирует
эти же функции под своими приватными именами.

Формат «Времени повтора» (``repeat_time``): ``"HH:MM"`` или
``"HH:MM|every_Nd"`` (интервал N дней для «каждые N дней»).
"""
from __future__ import annotations

import calendar as _calendar
import re as _re
from datetime import datetime, timedelta, timezone
from typing import Optional


def to_local_wall(iso: str, tz_offset: int) -> str:
    """Нормализовать сохранённую дату-время в НАИВНОЕ ЛОКАЛЬНОЕ настенное время
    'YYYY-MM-DDTHH:MM' (issue #143).

    PG отдаёт даты с явным offset'ом (обычно '+00:00'). Honor'им его
    (``astimezone`` → пояс пользователя) и отдаём наивное локальное время.
    Наивные строки уже считаются локальными. Date-only (без 'T') — без изменений.
    """
    if not iso:
        return iso
    s = str(iso).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "T" not in s:
        return s[:10]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s[:16]
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone(timedelta(hours=tz_offset)))
    return dt.strftime("%Y-%m-%dT%H:%M")


def parse_repeat_time(raw: str) -> tuple:
    """'HH:MM|every_Nd' → ('HH:MM', N). Возвращает ('HH:MM', 0) если интервала нет."""
    if not raw:
        return ("09:00", 0)
    if "|every_" in raw:
        parts = raw.split("|every_", 1)
        time_str = parts[0] or "09:00"
        m = _re.match(r"(\d+)d", parts[1])
        return (time_str, int(m.group(1)) if m else 0)
    m = _re.match(r"every_(\d+)d$", raw)
    if m:
        return ("09:00", int(m.group(1)))
    return (raw, 0)


def interval_label(interval_days: int) -> str:
    """Человекочитаемо: 'каждые 2 дня' / 'каждые 5 дней'."""
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


def next_cycle_date(current_date_str: str, repeat: str, tz_offset: int = 3,
                    interval_days: int = 0,
                    override_time: Optional[str] = None) -> str:
    """Дата следующего цикла для повторяющейся записи.

    base = max(old_date, today) — не прыгаем в прошлое если запись просрочена.
    Если входная строка содержит время (YYYY-MM-DDTHH:MM) — время сохраняется.
    Возвращает YYYY-MM-DD или YYYY-MM-DDTHH:MM.

    ``override_time`` (HH:MM) — каноническое время из «Времени повтора»; если
    задано, заменяет HH:MM из current_date_str (нужно после снуза напоминания).
    """
    if current_date_str:
        current_date_str = to_local_wall(current_date_str, tz_offset)
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
        if override_time and _re.match(r"^\d{2}:\d{2}$", override_time):
            time_part = override_time
        else:
            time_part = current_date_str.split("T")[1][:5]
        result = result + "T" + time_part
    return result
