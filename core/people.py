"""core/people.py — pure helpers of the People domain (ADR-0027)."""
from __future__ import annotations

from datetime import date
from typing import Optional

UNKNOWN_YEAR = 1904  # заглушка года в people.birthday, когда год неизвестен


def age(birthday: Optional[date], year_known: bool = True, today: Optional[date] = None) -> Optional[int]:
    """Полных лет на `today`. None, если ДР нет или год неизвестен
    (заглушка 1904 — не настоящий год рождения; считаем возраст — врём)."""
    if birthday is None or not year_known or birthday.year == UNKNOWN_YEAR:
        return None
    today = today or date.today()
    years = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    return years if years >= 0 else None


def birthday_iso(birthday: Optional[date], year_known: bool = True) -> Optional[str]:
    """'YYYY-MM-DD'; при неизвестном годе — 'MM-DD' c префиксом '--' (ISO 8601 partial),
    чтобы заглушка 1904 не утекала в API."""
    if birthday is None:
        return None
    if not year_known or birthday.year == UNKNOWN_YEAR:
        return "--%02d-%02d" % (birthday.month, birthday.day)
    return birthday.isoformat()
