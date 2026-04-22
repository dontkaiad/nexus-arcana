"""miniapp/backend/tarot.py — deck registry + card matcher.

Регистр колод и карт читается из
`miniapp/frontend/public/decks/deck_cards.json` (тот же файл что смотрит фронт).

Используется для:
- сопоставления произвольного ввода карты с канонической записью (en/ru/file);
- сериализации `cards` в ответе /api/arcana/sessions/{id} с картинками.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

_DECKS_JSON = (
    Path(__file__).parent.parent / "frontend" / "public" / "decks" / "deck_cards.json"
)

_decks_cache: Optional[dict] = None


def load_decks() -> dict:
    """Читает deck_cards.json; если файла нет — возвращает {}."""
    global _decks_cache
    if _decks_cache is not None:
        return _decks_cache
    try:
        with open(_DECKS_JSON, "r", encoding="utf-8") as f:
            _decks_cache = json.load(f)
    except FileNotFoundError:
        _decks_cache = {}
    return _decks_cache


def _clear_cache_for_tests() -> None:
    global _decks_cache
    _decks_cache = None


# Маппинг русского имени колоды (как хранится в Notion) → id папки
DECK_NAME_TO_ID = {
    "Уэйт": "rider-waite",
    "Таро Уэйта": "rider-waite",
    "Таро Райдера-Уэйта": "rider-waite",
    "Rider-Waite": "rider-waite",
    "Тёмный лес": "dark-wood",
    "Темный лес": "dark-wood",
    "Dark Wood": "dark-wood",
    "Луна отшельника": "deviant-moon",
    "Deviant Moon": "deviant-moon",
    "Ленорман": "lenormand",
    "Lenormand": "lenormand",
    "Атласные": "atlasnye",
}


def resolve_deck_id(deck_name: str | None) -> str:
    """Русское имя колоды из Notion → id папки. Дефолт — rider-waite."""
    if not deck_name:
        return "rider-waite"
    # точное совпадение
    if deck_name in DECK_NAME_TO_ID:
        return DECK_NAME_TO_ID[deck_name]
    # частичное (для 'Таро Уэйта, Ленорман')
    low = deck_name.lower()
    for k, v in DECK_NAME_TO_ID.items():
        if k.lower() in low:
            return v
    return "rider-waite"


def _cards_of(deck_id: str) -> list[dict]:
    decks = load_decks()
    deck = decks.get(deck_id)
    if not deck:
        return []
    if "cards_mirror" in deck:
        mirror = decks.get(deck["cards_mirror"])
        if mirror:
            return mirror.get("cards", [])
    return deck.get("cards", [])


def find_card(deck_id: str, query: str) -> Optional[dict]:
    """Ищет карту по свободному тексту. Возвращает {file, en, ru, aliases} или None."""
    if not query:
        return None
    cards = _cards_of(deck_id)
    if not cards:
        return None

    q = query.strip()
    q_low = q.lower()

    # 1. exact match по en / ru / aliases
    for c in cards:
        if q_low == c["en"].lower() or q_low == c["ru"].lower():
            return c
        for a in c.get("aliases", []):
            if q_low == a.lower():
                return c

    # 2. contains + все слова query должны быть в en|ru|aliases
    q_tokens = {w for w in re.split(r"[^\w]+", q_low) if w}
    if not q_tokens:
        return None

    def tokens_of(c: dict) -> set[str]:
        pool = " ".join([c.get("en", ""), c.get("ru", ""), *c.get("aliases", [])]).lower()
        return {w for w in re.split(r"[^\w]+", pool) if w}

    # best-effort: наибольшее пересечение
    best = None
    best_score = 0
    for c in cards:
        cts = tokens_of(c)
        if q_tokens.issubset(cts):
            score = len(q_tokens)
            if score > best_score:
                best = c
                best_score = score
    return best


def canonical_card(deck_id: str, query: str) -> dict:
    """Каноническое имя для UI.

    Возвращает:
      {matched: True, en, ru, file, deck_id} — если нашли,
      {matched: False, raw: "что юзер ввёл", deck_id}  — если нет.
    """
    c = find_card(deck_id, query)
    if c:
        return {
            "matched": True,
            "en": c["en"],
            "ru": c["ru"],
            "file": c["file"],
            "deck_id": deck_id,
        }
    return {"matched": False, "raw": query.strip(), "deck_id": deck_id}


def parse_cards_raw(raw: str, deck_id: str) -> list[dict]:
    """Разделяет сырой ввод ('Шут, Маг' или с переносами) → список canonical."""
    if not raw:
        return []
    # разделители: запятая, перенос, точка с запятой, пайп
    parts = re.split(r"[\n;,|]+", raw)
    return [canonical_card(deck_id, p) for p in parts if p.strip()]
