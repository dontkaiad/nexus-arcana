"""miniapp/backend/_moon.py — фаза луны по приблизительной синодической формуле.

Ошибка: ±0.5 дня — достаточно для UI-фазы, не для астрологии.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

MOON_GLYPHS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
MOON_NAMES = [
    "Новолуние", "Растущий серп", "Первая четверть", "Растущая луна",
    "Полнолуние", "Убывающая луна", "Последняя четверть", "Убывающий серп",
]

_KNOWN_NEW = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
_SYNODIC_SEC = 29.530588853 * 86400

# Крупные фазы: 0 = Новолуние, 2 = Первая четверть, 4 = Полнолуние, 6 = Последняя четверть
_MAJOR_PHASES = [0, 2, 4, 6]


def moon_phase(dt: Optional[datetime] = None) -> dict:
    """Возвращает фазу луны на указанный (или текущий UTC) момент.

    dict: {"idx": 0..7, "glyph": "🌕", "name": "Полнолуние",
           "days": 0..29, "illum": 0..100}
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff_sec = (dt - _KNOWN_NEW).total_seconds()
    frac = (diff_sec % _SYNODIC_SEC) / _SYNODIC_SEC
    idx = round(frac * 8) % 8
    days = round(frac * 29.53)
    illum = round((1 - math.cos(frac * 2 * math.pi)) * 50)
    return {
        "idx": idx,
        "glyph": MOON_GLYPHS[idx],
        "name": MOON_NAMES[idx],
        "days": days,
        "illum": illum,
    }


def next_phases(count: int = 4, start: Optional[datetime] = None) -> list[dict]:
    """Возвращает список следующих крупных фаз луны (новолуние, четверти, полнолуние).

    Каждый элемент: {"idx": int, "glyph": str, "name": str, "date": "YYYY-MM-DD"}
    """
    if start is None:
        start = datetime.now(timezone.utc)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    diff_sec = (start - _KNOWN_NEW).total_seconds()
    # сколько полных синодических циклов прошло
    cycles_passed = diff_sec // _SYNODIC_SEC
    results: list[dict] = []
    cycle = int(cycles_passed)
    while len(results) < count:
        for mp in _MAJOR_PHASES:
            phase_frac = mp / 8  # 0, 0.25, 0.5, 0.75
            phase_time = _KNOWN_NEW + timedelta(seconds=(cycle + phase_frac) * _SYNODIC_SEC)
            if phase_time >= start:
                results.append({
                    "idx": mp,
                    "glyph": MOON_GLYPHS[mp],
                    "name": MOON_NAMES[mp],
                    "date": phase_time.date().isoformat(),
                })
                if len(results) >= count:
                    break
        cycle += 1
    return results
