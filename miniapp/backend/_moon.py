"""miniapp/backend/_moon.py — фаза луны по приблизительной синодической формуле.

Ошибка: ±0.5 дня — достаточно для UI-фазы, не для астрологии.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

MOON_GLYPHS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
MOON_NAMES = [
    "Новолуние", "Растущий серп", "Первая четверть", "Растущая луна",
    "Полнолуние", "Убывающая луна", "Последняя четверть", "Убывающий серп",
]

_KNOWN_NEW = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
_SYNODIC_SEC = 29.530588853 * 86400


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
