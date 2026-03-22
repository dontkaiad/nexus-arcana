"""core/praise.py — случайные фразы похвалы по контексту."""
from __future__ import annotations

import random
from typing import List

_PHRASES: dict[str, List[str]] = {
    "finance_under_limit": [
        "💪 Так держать, финансовая дисциплина на высоте!",
        "✨ Молодец что следишь за расходами!",
        "🌟 Отличный результат по бюджету!",
        "💚 Экономия засчитана!",
    ],
}


def get_praise(context: str) -> str:
    """Вернуть случайную фразу для контекста. Если контекст неизвестен — пустая строка."""
    options = _PHRASES.get(context, [])
    return random.choice(options) if options else ""
