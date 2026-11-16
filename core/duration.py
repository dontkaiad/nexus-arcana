"""core/duration.py — парсинг длительности из человеческого текста (#241).

Общий для Nexus (✅ Задачи) и Arcana (🔮 Работы) — обе стороны дают
пользователю задавать duration_min через NL edit-flow ("поставь длительность
2 часа для встречи с Тёмычем"). Регексом, не Haiku — формат простой, жалко
токенов (см. CLAUDE.md: "Claude API там где регекс справится").
"""
from __future__ import annotations

import re
from typing import Optional

_HOURS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:ч(?:ас(?:а|ов)?)?)\b", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)\s*(?:мин(?:ут(?:а|ы)?)?)\b", re.IGNORECASE)
_HALF_HOUR_RE = re.compile(r"\bполчас[а-я]*\b", re.IGNORECASE)
_HOUR_ALONE_RE = re.compile(r"^\s*час[а-я]*\s*$", re.IGNORECASE)
# «с 14 до 17», «с 14:30 до 16», «14–17»: длительность = конец − начало (в тех же сутках).
# «10–12 минут» / «2–3 часа» — не диапазон времени суток (после чисел стоит единица) → regexp не срабатывает.
_RANGE_RE = re.compile(
    r"(?:\bс\s*)?\b(\d{1,2})(?:[:.](\d{2}))?\s*(?:до|по|[-–—])\s*(\d{1,2})(?:[:.](\d{2}))?\b(?!\s*(?:мин|ч\b|час))",
    re.IGNORECASE,
)


def _range_minutes(t: str) -> Optional[int]:
    """«с 14 до 17» → 180. None, если это не диапазон времени суток или конец не позже начала
    (через полночь не считаем — это скорее опечатка, чем длительность)."""
    m = _RANGE_RE.search(t)
    if not m:
        return None
    h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
    if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
        return None
    minutes = (h2 * 60 + m2) - (h1 * 60 + m1)
    return minutes if minutes > 0 else None


def parse_duration_minutes(text: str) -> Optional[int]:
    """"2 часа" → 120, "30 минут" → 30, "1.5 часа" → 90, "1 час 30 минут" → 90,
    "полчаса" → 30, "час" → 60, "с 14 до 17" → 180. None, если ничего не распознала."""
    if not text or not text.strip():
        return None
    t = text.strip().lower()

    rng = _range_minutes(t)
    if rng is not None:
        return rng
    if _HALF_HOUR_RE.search(t):
        return 30
    if _HOUR_ALONE_RE.match(t):
        return 60

    total = 0.0
    found = False
    m = _HOURS_RE.search(t)
    if m:
        total += float(m.group(1).replace(",", ".")) * 60
        found = True
    m = _MINUTES_RE.search(t)
    if m:
        total += float(m.group(1))
        found = True

    if not found:
        return None
    minutes = round(total)
    return minutes if minutes > 0 else None


def format_duration(minutes: int) -> str:
    """120 → "2 ч", 90 → "1 ч 30 мин", 30 → "30 мин"."""
    if minutes <= 0:
        return "0 мин"
    h, m = divmod(minutes, 60)
    parts = []
    if h:
        parts.append(f"{h} ч")
    if m:
        parts.append(f"{m} мин")
    return " ".join(parts) or "0 мин"
