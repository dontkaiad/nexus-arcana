"""arcana/tarot_loader.py — загрузка значений карт из справочников по колоде."""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger("arcana.tarot_loader")

REFS_DIR = os.path.join(os.path.dirname(__file__), "tarot_refs")

DECK_FILES: Dict[str, str] = {
    "уэйта":          "waite.json",
    "уэйт":           "waite.json",
    "райдер":         "waite.json",
    "rider":          "waite.json",
    "waite":          "waite.json",
    "dark wood":      "dark_wood.json",
    "тёмный лес":     "dark_wood.json",
    "темный лес":     "dark_wood.json",
    "дарк вуд":       "dark_wood.json",
    "deviant moon":   "deviant_moon.json",
    "безумная луна":  "deviant_moon.json",
    "девиант":        "deviant_moon.json",
    "ленорман":       "lenormand.json",
    "lenormand":      "lenormand.json",
    "игральные":      "playing_cards.json",
}

# Секции в waite / dark_wood / deviant_moon
_SUIT_SECTIONS = ("Старшие Арканы", "Жезлы", "Кубки", "Мечи", "Пентакли")
# Масти в playing_cards
_PLAYING_SUITS = ("Пики", "Трефы", "Червы", "Бубны")

_cache: Dict[str, dict] = {}


def _load_deck(filename: str) -> dict:
    if filename in _cache:
        return _cache[filename]
    path = os.path.join(REFS_DIR, filename)
    if not os.path.exists(path):
        logger.warning("Файл колоды не найден: %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache[filename] = data
        return data
    except Exception as e:
        logger.error("Ошибка загрузки %s: %s", path, e)
        return {}


def get_deck_file(deck_name: str) -> Optional[str]:
    deck_lower = deck_name.lower().strip()
    for key, filename in DECK_FILES.items():
        if key in deck_lower:
            return filename
    return None


def get_deck_style(deck_name: str) -> str:
    styles = _load_deck("deck_styles.json")
    deck_lower = deck_name.lower()
    for key, data in styles.items():
        if isinstance(data, dict) and (
            key.lower() in deck_lower or deck_lower in key.lower()
        ):
            return data.get("style", "")
    return ""


def get_cards_context(deck_name: str, card_names: List[str]) -> str:
    """Возвращает значения ТОЛЬКО запрошенных карт из справочника для вставки в промпт."""
    if not card_names:
        return ""
    filename = get_deck_file(deck_name)
    if not filename:
        logger.debug("Неизвестная колода: %s", deck_name)
        return ""
    deck_data = _load_deck(filename)
    if not deck_data:
        return ""

    found: List[str] = []
    for card_name in card_names:
        card_info = _find_card(deck_data, card_name.lower().strip())
        if card_info:
            found.append(f"📍 {card_name}:\n{_format_card_info(card_info)}")

    if not found:
        return ""

    style = get_deck_style(deck_name)
    header = f"Колода: {deck_name}"
    if style:
        header += f"\nСтиль: {style}"
    return header + "\n\n" + "\n\n".join(found)


def _find_card(deck_data: dict, card_lower: str) -> Optional[dict]:
    """Нечёткий поиск карты во всех форматах справочников."""
    # 1. Секционный формат (waite, dark_wood, deviant_moon)
    for section in _SUIT_SECTIONS:
        for key, val in deck_data.get(section, {}).items():
            if card_lower in key.lower():
                return val if isinstance(val, dict) else {"up": str(val)}

    # 2. Масти (playing_cards)
    for suit in _PLAYING_SUITS:
        for key, val in deck_data.get(suit, {}).items():
            if card_lower in key.lower():
                return val if isinstance(val, dict) else {"meaning": str(val)}

    # 3. Верхний уровень без метаданных (lenormand)
    for key, val in deck_data.items():
        if key.startswith("_"):
            continue
        if card_lower in key.lower() and isinstance(val, dict):
            return val

    return None


def _format_card_info(info: dict) -> str:
    parts: List[str] = []
    for key, val in info.items():
        if key.startswith("_") or key == "error":
            continue
        if isinstance(val, str) and val:
            parts.append(f"  {key}: {val[:600]}")
    return "\n".join(parts)
