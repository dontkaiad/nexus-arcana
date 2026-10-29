"""zarya/formatting.py — pure helpers for Zarya (dates, slot labels, intent).

No aiogram / DB imports here so it stays trivially testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

MSK = timezone(timedelta(hours=3))

_DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
# предложный/винительный падеж — «в понедельник», «во вторник», «в среду»...
_DOW_PHRASE = [
    "в понедельник", "во вторник", "в среду", "в четверг",
    "в пятницу", "в субботу", "в воскресенье",
]


def day_phrase(d: date) -> str:
    return _DOW_PHRASE[d.weekday()]

# «когда у Кай окно», «свободные слоты», «когда свободна», «запиши меня»…
_ASK_SLOTS_RE = re.compile(
    r"(свободн\w*|окн[оа]\w*|когда\b.*\b(кай|занят|можн)|слот\w*|запиш\w*|записаться|"
    r"забронир\w*|когда\s+можно|есть\s+время)",
    re.IGNORECASE,
)


def wants_slots(text: str) -> bool:
    return bool(_ASK_SLOTS_RE.search(text or ""))


# #235: «есть слоты на вторник» — раньше отдавала общий список на 4 дня
# вперёд, даже если вторник туда не попадал. Теперь понимает КАКОЙ день
# спрашивают и отвечает конкретно про него.
_WEEKDAY_RE = [
    (re.compile(r"понедельник\w*", re.IGNORECASE), 0),
    (re.compile(r"вторник\w*", re.IGNORECASE), 1),
    (re.compile(r"сред\w*", re.IGNORECASE), 2),
    (re.compile(r"четверг\w*", re.IGNORECASE), 3),
    (re.compile(r"пятниц\w*", re.IGNORECASE), 4),
    (re.compile(r"суббот\w*", re.IGNORECASE), 5),
    (re.compile(r"воскресень\w*", re.IGNORECASE), 6),
]
_POSLEZAVTRA_RE = re.compile(r"послезавтра", re.IGNORECASE)
_ZAVTRA_RE = re.compile(r"\bзавтра\b", re.IGNORECASE)
_SEGODNYA_RE = re.compile(r"сегодня", re.IGNORECASE)


def extract_asked_date(text: str, today: date) -> Optional[date]:
    """Если в тексте явно назван день (сегодня/завтра/послезавтра/день недели)
    — вернуть его дату. Название дня недели без уточнения «на следующей
    неделе» — ближайшее вхождение (сегодня, если сегодня тот же день)."""
    t = text or ""
    if _POSLEZAVTRA_RE.search(t):
        return today + timedelta(days=2)
    if _ZAVTRA_RE.search(t):
        return today + timedelta(days=1)
    if _SEGODNYA_RE.search(t):
        return today
    for pattern, wd in _WEEKDAY_RE:
        if pattern.search(t):
            delta = (wd - today.weekday()) % 7
            return today + timedelta(days=delta)
    return None


def slot_label(start: datetime, *, hours: float = 1.0) -> str:
    s = start.astimezone(MSK)
    e = s + timedelta(hours=hours)
    return f"{_DOW[s.weekday()]} {s.day:02d}.{s.month:02d} {s:%H:%M}–{e:%H:%M}"


def day_header(d: datetime) -> str:
    s = d.astimezone(MSK)
    return f"{_DOW[s.weekday()]} {s.day:02d}.{s.month:02d}"


def group_slots_by_day(slots: List[datetime], limit_days: int = 4) -> "list[tuple[str, list[datetime]]]":
    out: "list[tuple[str, list[datetime]]]" = []
    for s in slots:
        h = day_header(s)
        if not out or out[-1][0] != h:
            if len(out) >= limit_days:
                break
            out.append((h, []))
        out[-1][1].append(s)
    return out


def epoch(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def from_epoch(v: str | int) -> datetime:
    return datetime.fromtimestamp(int(v), tz=timezone.utc)


# ── one-shot booking parser: «завтра в 19 шашлыки», «в пятницу с 19 до 23 др» ─
# Детерминированно, без LLM. Нужны дата + время; остаток текста — повод.

_DATE_WORDS = (
    r"послезавтра|завтра|сегодня|понедельник\w*|вторник\w*|сред\w*|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*"
)
_DATE_STRIP_RE = re.compile(rf"(?:\b(?:в|во|на)\s+)?\b(?:{_DATE_WORDS})", re.IGNORECASE)
_RANGE_RE = re.compile(
    r"(?:\bс\s+)?\b(\d{1,2})(?::(\d{2}))?\s*(?:до|-|–)\s*(\d{1,2})(?::(\d{2}))?\b",
    re.IGNORECASE,
)
_AT_RE = re.compile(r"\b(?:в|к)\s+(\d{1,2})(?::(\d{2}))?(?:\s*(?:ч\b|час\w*))?(?!\d)", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"(?:\bна\s+)?\b(\d+(?:[.,]\d+)?)\s*(?:ч\b|час\w*)", re.IGNORECASE)
_FILLER_RE = re.compile(
    r"\b(?:запиши|записать|запишите|забронируй|забронировать|поставь|меня|мне|пожалуйста|плиз)\b",
    re.IGNORECASE,
)


@dataclass
class QuickBooking:
    day: date
    hour: int
    minute: int
    hours: Optional[float]  # None → не названа
    note: str

    def start(self) -> datetime:
        return datetime.combine(self.day, time(self.hour, self.minute), tzinfo=MSK)


def parse_quick_booking(text: str, today: date) -> Optional[QuickBooking]:
    """Дата (extract_asked_date) + время («в 19», «с 19 до 23») → QuickBooking.
    Нет даты или времени → None (идёт обычный показ слотов)."""
    t = text or ""
    day = extract_asked_date(t, today)
    if day is None:
        return None
    hours: Optional[float] = None
    m = _RANGE_RE.search(t)
    if m:
        h1, m1 = int(m.group(1)), int(m.group(2) or 0)
        h2, m2 = int(m.group(3)), int(m.group(4) or 0)
        if not (0 <= h1 < 24 and 0 <= h2 <= 24 and m1 < 60 and m2 < 60):
            return None
        span = (h2 * 60 + m2) - (h1 * 60 + m1)
        if span <= 0:
            span += 24 * 60
        hours = span / 60
        hour, minute = h1, m1
        t = t[:m.start()] + " " + t[m.end():]
    else:
        m = _AT_RE.search(t)
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if hour > 23 or minute > 59:
            return None
        t = t[:m.start()] + " " + t[m.end():]
        d = _DURATION_RE.search(t)
        if d:
            hours = float(d.group(1).replace(",", "."))
            t = t[:d.start()] + " " + t[d.end():]
    if hours is not None and not (0 < hours <= 12):
        return None
    t = _DATE_STRIP_RE.sub(" ", t)
    t = _FILLER_RE.sub(" ", t)
    note = re.sub(r"\s+", " ", t).strip(" ,.;:—-")
    return QuickBooking(day=day, hour=hour, minute=minute, hours=hours, note=note)
