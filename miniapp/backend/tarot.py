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


# Алиасы названий колод → id папки. Все ключи в lowercase.
DECK_ALIASES: dict[str, str] = {
    "уэйт": "rider-waite", "уэйта": "rider-waite", "уэйту": "rider-waite",
    "таро уэйта": "rider-waite", "таро райдера-уэйта": "rider-waite",
    "rider": "rider-waite", "rider-waite": "rider-waite", "rider waite": "rider-waite",
    "тёмный лес": "dark-wood", "темный лес": "dark-wood", "dark wood": "dark-wood",
    "darkwood": "dark-wood", "dark-wood": "dark-wood", "дарквуд": "dark-wood",
    "дарк вуд": "dark-wood",
    "луна отшельника": "deviant-moon", "deviant": "deviant-moon",
    "deviant moon": "deviant-moon", "deviant-moon": "deviant-moon",
    "девиант мун": "deviant-moon",
    "ленорман": "lenormand", "lenormand": "lenormand",
    "игральные": "atlasnye", "атласные": "atlasnye", "playing": "atlasnye",
    "playing cards": "atlasnye", "atlasnye": "atlasnye",
}

# Backwards-compat: старое имя экспорта.
DECK_NAME_TO_ID = DECK_ALIASES


def resolve_deck_id(deck_name: str | None) -> str:
    """Свободный ввод имени колоды → id папки. Дефолт — rider-waite.

    Сверяет с реестром deck_cards.json (по ключу, name_ru, name_en) +
    DECK_ALIASES (lowercase). Подстрочное совпадение тоже работает.
    """
    if not deck_name:
        return "rider-waite"
    low = deck_name.strip().lower()
    if not low:
        return "rider-waite"

    # 1. exact alias match
    if low in DECK_ALIASES:
        return DECK_ALIASES[low]

    # 2. реестр: по key / name_ru / name_en
    decks = load_decks()
    for key, info in decks.items():
        if key.startswith("_"):
            continue
        if not isinstance(info, dict):
            continue
        if low == key.lower():
            return key
        if low == (info.get("name_ru") or "").lower():
            return key
        if low == (info.get("name_en") or "").lower():
            return key

    # 3. подстрочный поиск по алиасам (для строк вида 'Таро Уэйта, Ленорман')
    for alias, key in DECK_ALIASES.items():
        if alias in low or low in alias:
            return key

    # 4. подстрочный поиск по реестру
    for key, info in decks.items():
        if key.startswith("_") or not isinstance(info, dict):
            continue
        for field in ("name_ru", "name_en"):
            v = (info.get(field) or "").lower()
            if v and (v in low or low in v):
                return key

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


# ── Числовые/мастевые алиасы для коротких пользовательских форматов ────────

NUM_TO_WORD: dict[str, str] = {
    "1":  "туз",       "ace":   "туз",
    "2":  "двойка",    "two":   "двойка",
    "3":  "тройка",    "three": "тройка",
    "4":  "четвёрка",  "four":  "четвёрка",
    "5":  "пятёрка",   "five":  "пятёрка",
    "6":  "шестёрка",  "six":   "шестёрка",
    "7":  "семёрка",   "seven": "семёрка",
    "8":  "восьмёрка", "eight": "восьмёрка",
    "9":  "девятка",   "nine":  "девятка",
    "10": "десятка",   "ten":   "десятка",
    "11": "паж",       "page":  "паж",
    "12": "рыцарь",    "knight": "рыцарь",
    "13": "королева",  "queen": "королева",
    "14": "король",    "king":  "король",
}

SUIT_FULL: dict[str, str] = {
    "пентаклей": "пентаклей", "пент": "пентаклей", "монет": "пентаклей",
    "кубков":    "кубков",    "куб":  "кубков",    "чаш":   "кубков",
    "мечей":     "мечей",     "меч":  "мечей",
    "жезлов":    "жезлов",    "жез":  "жезлов",    "посохов": "жезлов",
    "pentacles": "пентаклей", "coins": "пентаклей",
    "cups":      "кубков",
    "swords":    "мечей",
    "wands":     "жезлов",
}

_RANK_WORDS = "ace|page|knight|queen|king|two|three|four|five|six|seven|eight|nine|ten"
_NUM_RE = re.compile(
    rf"^(\d+|{_RANK_WORDS})\s+(?:of\s+)?([а-яёa-z]+)$",
    re.IGNORECASE,
)


def normalize_card_input(text: str) -> str:
    """'9 пентаклей' → 'девятка пентаклей', 'Nine of Pentacles' → 'девятка пентаклей'.

    Если паттерн не подошёл (например 'шут', 'The Fool', 'королева кубков') —
    возвращает текст как есть, чтобы не ломать уже нормальные имена.
    """
    if not text:
        return ""
    s = text.strip().lower()
    m = _NUM_RE.match(s)
    if not m:
        return s
    rank, suit = m.group(1).lower(), m.group(2).lower()
    rank_word = NUM_TO_WORD.get(rank, rank)
    suit_full = SUIT_FULL.get(suit, suit)
    return f"{rank_word} {suit_full}"


def find_card(deck_id: str, query: str) -> Optional[dict]:
    """Ищет карту по свободному тексту. Возвращает {file, en, ru, aliases} или None."""
    if not query:
        return None
    cards = _cards_of(deck_id)
    if not cards:
        return None

    q = normalize_card_input(query)
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
