"""zarya/formatting.py — pure helpers for Zarya (dates, slot labels, intent).

No aiogram / DB imports here so it stays trivially testable.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
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
