"""zarya/formatting.py — pure helpers for Zarya (dates, slot labels, intent).

No aiogram / DB imports here so it stays trivially testable.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import List

MSK = timezone(timedelta(hours=3))

_DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# «когда у Кай окно», «свободные слоты», «когда свободна», «запиши меня»…
_ASK_SLOTS_RE = re.compile(
    r"(свободн\w*|окн[оа]\w*|когда\b.*\b(кай|занят|можн)|слот\w*|запиш\w*|записаться|"
    r"забронир\w*|когда\s+можно|есть\s+время)",
    re.IGNORECASE,
)


def wants_slots(text: str) -> bool:
    return bool(_ASK_SLOTS_RE.search(text or ""))


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
