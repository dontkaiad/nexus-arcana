"""core/layout.py — конвертер раскладки EN→RU (QWERTY→ЙЦУКЕН).

Smart guard (май 2026):
1. Brand whitelist — если в тексте есть слово из набора брендов (Apple,
   iPhone, iPiter, Ozon, …) — НЕ конвертируем.
2. Mixed-script — если хотя бы один токен содержит И кириллицу И латиницу
   (например «Apple-стек») — НЕ конвертируем.
3. Real-English — если в тексте ≥2 латинских слов длиной ≥3 С ГЛАСНЫМИ
   (т.е. реально похожих на английские, а не «pflfxf»-мусор раскладки)
   — НЕ конвертируем.
4. Только если все три guard'а пропустили — старая логика по доле
   кириллицы (порог 0.3 для skip, 0.5 для accept после конверсии).

Без этих guard'ов сообщения с латинскими брендами + кириллицей шли в
конвертер целиком и превращались в нечитаемую кашу, ломая pipeline списков.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── EN→RU mapping ────────────────────────────────────────────────────────────

_MAPPING = {
    'q':'й','w':'ц','e':'у','r':'к','t':'е','y':'н','u':'г','i':'ш','o':'щ','p':'з',
    '[':'х',']':'ъ',
    'a':'ф','s':'ы','d':'в','f':'а','g':'п','h':'р','j':'о','k':'л','l':'д',
    ';':'ж',"'":'э',
    'z':'я','x':'ч','c':'с','v':'м','b':'и','n':'т','m':'ь',',':'б','.':'ю',
    'Q':'Й','W':'Ц','E':'У','R':'К','T':'Е','Y':'Н','U':'Г','I':'Ш','O':'Щ','P':'З',
    '{':'Х','}':'Ъ',
    'A':'Ф','S':'Ы','D':'В','F':'А','G':'П','H':'Р','J':'О','K':'Л','L':'Д',
    ':':'Ж','Z':'Я','X':'Ч','C':'С','V':'М','B':'И','N':'Т','M':'Ь','<':'Б','>':'Ю',
}

EN2RU = str.maketrans(_MAPPING)


# ── Brand whitelist (alphabetical) ────────────────────────────────────────────

_BRAND_WHITELIST = frozenset({
    # Apple
    "airpods", "airtag", "apple", "imac", "ipad", "iphone", "ipod", "mac",
    "macbook", "watch",
    # Apple-сервисы / магазины ремонта
    "icloud", "ipiter", "pedant",
    # Google / Android / производители
    "android", "google", "huawei", "oneplus", "pixel", "redmi", "samsung",
    "xiaomi",
    # Бренды одежды / косметики (часто пишет Кай)
    "adidas", "asics", "charlotte", "chanel", "diptyque", "lamoda", "mango",
    "nike", "ralph", "reserved", "tilbury", "uniqlo", "zara",
    # Магазины РФ / ecommerce
    "aliexpress", "avito", "dns", "lenta", "magnit", "ozon", "pyaterochka",
    "wb", "wildberries", "yandex",
    # Софт / сервисы
    "anthropic", "chatgpt", "claude", "github", "gitlab", "gmail",
    "instagram", "notion", "openai", "spotify", "telegram", "tiktok",
    "twitter", "vk", "whatsapp", "youtube",
    # Атрибуты / варианты (часто рядом с брендом)
    "lite", "max", "mini", "plus", "premium", "pro", "ultra",
})


# Regex выделения «слов» — кириллица + латиница + цифры. Дефис/слэш считаются
# разделителями (поэтому «Apple-стек» дробится на «Apple» и «стек», но сам
# токен по \s остаётся «Apple-стек» — его проверяет mixed-script.).
_WORD_RE = re.compile(r"[A-Za-zЀ-ӿ0-9]+")
_LATIN_RE = re.compile(r"^[A-Za-z]+$")
_VOWELS = set("aeiouyAEIOUY")


def _ru_ratio(text: str) -> float:
    ru = sum(1 for c in text if 'Ѐ' <= c <= 'ӿ')
    en = sum(1 for c in text if c.isalpha() and c.isascii())
    total = ru + en
    return ru / total if total else 0.0


def _has_brand(text: str) -> bool:
    """True если в тексте хоть одно слово из brand whitelist (case-insensitive)."""
    for w in _WORD_RE.findall(text):
        if w.lower() in _BRAND_WHITELIST:
            return True
    return False


def _has_mixed_script_token(text: str) -> bool:
    """True если хотя бы один whitespace-токен содержит и латиницу и кириллицу
    (например «Apple-стек», «iPiter/премиум»).
    """
    for token in text.split():
        has_lat = any('a' <= c.lower() <= 'z' for c in token)
        has_cyr = any('Ѐ' <= c <= 'ӿ' for c in token)
        if has_lat and has_cyr:
            return True
    return False


def _looks_english(word: str) -> bool:
    """Похоже ли латинское слово на реальное английское: длина ≥3 + ≥1 гласная.

    Это отсекает «pflfxf»/«vjkjrj» (раскладка-мусор без гласных) от реальных
    «iPhone»/«Apple»/«Pro» (с гласными).
    """
    if len(word) < 3 or not _LATIN_RE.match(word):
        return False
    return any(c in _VOWELS for c in word)


def _english_word_count(text: str) -> int:
    return sum(1 for w in _WORD_RE.findall(text) if _looks_english(w))


def maybe_convert(text: str) -> str:
    """Если текст похож на русский, набранный в EN раскладке — конвертирует.

    Иначе возвращает как есть. Все ветки логируются (DEBUG для skip,
    INFO для реальной конверсии) — это критично для диагностики, иначе
    баги типа «Apple → Фззду» приходится ловить по скринам.
    """
    if not text or not text.strip():
        return text or ""

    # Если в тексте вообще нет букв (только цифры/пунктуация) — нечего конвертить.
    if not any(c.isalpha() for c in text):
        return text

    preview = text[:50].replace("\n", " ")

    # ── Guard 1: brand whitelist
    if _has_brand(text):
        logger.debug("layout: skip (brand_whitelist) — %r", preview)
        return text

    # ── Guard 2: mixed-script token («Apple-стек»)
    if _has_mixed_script_token(text):
        logger.debug("layout: skip (mixed_script) — %r", preview)
        return text

    # ── Guard 3: ≥2 латинских слов которые выглядят как реальный English
    en_count = _english_word_count(text)
    if en_count >= 2:
        logger.debug(
            "layout: skip (english_words=%d) — %r", en_count, preview,
        )
        return text

    # ── Старая ratio-логика
    ru_before = _ru_ratio(text)
    if ru_before > 0.3:
        logger.debug(
            "layout: skip (already_ru ratio=%.2f) — %r", ru_before, preview,
        )
        return text

    converted = text.translate(EN2RU)
    ru_after = _ru_ratio(converted)
    if ru_after > 0.5:
        logger.info(
            "layout: convert ru=%.2f→%.2f — %r → %r",
            ru_before, ru_after, preview, converted[:50].replace("\n", " "),
        )
        return converted

    logger.debug(
        "layout: skip (low_after ratio=%.2f) — %r", ru_after, preview,
    )
    return text
