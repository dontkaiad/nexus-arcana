"""Универсальный хелпер для Select/Multi-select полей Notion.
Используется одинаково в Nexus и Arcana."""
import re
from typing import List, Tuple, Optional

OPTION_EMOJI = {
    # Заметки
    "идея": "💡", "мысль": "🧠", "практика": "🔮", "таро": "🃏",
    "ленорман": "🃏", "ритуал": "🕯️", "расходники": "🕯️", "рецепт": "🍳",
    "здоровье": "❤️", "финансы": "💰", "расклады": "🃏", "обучение": "📚",
    # Задачи/финансы
    "коты": "🐾", "жилье": "🏠", "привычки": "🚬", "продукты": "🍜",
    "транспорт": "🚕", "бьюти": "💅", "гардероб": "👗", "подписки": "💻",
    "прочее": "💳", "зарплата": "💰",
    # Arcana
    "личный": "🌟", "клиентский": "🤝",
}


def strip_emoji(s: str) -> str:
    """Убрать эмодзи и пробелы в начале строки."""
    return re.sub(r'^[\U00010000-\U0010ffff\u2600-\u27ff\u2300-\u23ff\s]+', '', s).strip()


def format_option(raw: str) -> str:
    """Форматировать опцию: Emoji Слово_с_заглавной.
    "расклады" → "🃏 Расклады"
    "🃏 расклады" → "🃏 Расклады"
    "неизвестное" → "Неизвестное"
    """
    clean = strip_emoji(raw)
    if not clean:
        return raw.strip()
    word_lower = clean.lower()
    emoji = OPTION_EMOJI.get(word_lower, "")
    return f"{emoji} {clean.capitalize()}" if emoji else clean.capitalize()


async def find_or_prepare(
    db_id: str, field: str, raw: str
) -> Tuple[str, bool]:
    """Найти опцию в существующих или подготовить новую.
    Возвращает (значение, is_new).
    is_new=False → существующая опция (брать как есть)
    is_new=True  → новая опция (требует подтверждения)
    """
    from core.notion_client import get_db_options
    existing = await get_db_options(db_id, field)
    raw_clean = strip_emoji(raw).lower()
    for opt in existing:
        if raw_clean == strip_emoji(opt).lower():
            return opt, False
        if raw_clean in strip_emoji(opt).lower() or strip_emoji(opt).lower() in raw_clean:
            return opt, False
    return format_option(raw), True


def confirm_keyboard(uid: int, new_opts: List[str], existing: List[str]):
    """InlineKeyboard для подтверждения новых опций."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    new_str = " ".join(f"#{o}" for o in new_opts)
    rows = [
        [InlineKeyboardButton(text=f"✅ Добавить {new_str}", callback_data=f"opt_add:{uid}")],
        [InlineKeyboardButton(text="📋 Выбрать из существующих", callback_data=f"opt_pick:{uid}")],
        [InlineKeyboardButton(text="💾 Сохранить без новых", callback_data=f"opt_skip:{uid}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pick_keyboard(uid: int, existing: List[str]):
    """InlineKeyboard со списком существующих опций."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=opt, callback_data=f"opt_sel:{uid}:{opt}")]
        for opt in existing[:10]
    ] + [[InlineKeyboardButton(text="✅ Готово", callback_data=f"opt_done:{uid}")]])
