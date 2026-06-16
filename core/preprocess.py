"""core/preprocess.py — общий пайплайн нормализации текста для обоих ботов.

Шаги:
1. ``maybe_convert`` — раскладка EN→RU (QWERTY→ЙЦУКЕН).
2. spell-correction через Haiku с **whitelist guard** (имена клиентов из
   Notion + 78 карт Таро + ~30 эзо-терминов).
3. anti-conversational + length guard (как в Nexus).

Кеш whitelist: SQLite ``spell_whitelist_cache``, TTL=1h. При создании
нового клиента caller обязан вызвать :func:`invalidate_whitelist`,
иначе свежее имя будет «исправлено» Haiku до окончания TTL.

Используется в:
- ``nexus/nexus_bot.py::process_text`` (заменил inline spell)
- ``arcana/handlers/base.py::route_message`` (новое — раньше вообще
  не было spell в Аркане)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Optional

from core.claude_client import ask_claude
from core.layout import maybe_convert

logger = logging.getLogger("core.preprocess")

_WHITELIST_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "spell_whitelist.db",
)
_WHITELIST_TTL = 3600  # 1 hour


# ── Static whitelists ────────────────────────────────────────────────────────

def _tarot_card_names_ru() -> list[str]:
    """Все 78 имён карт RU из rider-waite (минимум один deck)."""
    try:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "deck_cards.json",
        )
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        names: list[str] = []
        for deck in data.values():
            for card in deck.get("cards", []):
                ru = (card.get("ru") or "").strip()
                if ru and ru not in names:
                    names.append(ru)
        return names
    except Exception as e:
        logger.warning("tarot_card_names_ru load failed: %s", e)
        return []


_ESO_TERMS: list[str] = [
    # практика
    "расклад", "ритуал", "приворот", "очищение", "защита", "гримуар",
    "оракул", "медиум", "таро", "руны", "руна", "рунический",
    # энергии
    "чакра", "мантра", "аура", "карма", "энергия", "поток",
    # объекты
    "амулет", "талисман", "свеча", "колода", "дно",
    # действия
    "чистка", "открытие", "закрытие", "портал", "сброс",
    # карты-метаданные
    "перевёрнутая", "перевернутая", "прямая", "триплет",
    # бизнес
    "бартер", "гадание", "сеанс", "сессия",
    # типы клиентов
    "Self", "Платный", "Бесплатный",
]


def _static_whitelist() -> list[str]:
    return _tarot_card_names_ru() + _ESO_TERMS


# ── Cache (SQLite) ───────────────────────────────────────────────────────────

def _cache_db() -> sqlite3.Connection:
    con = sqlite3.connect(_WHITELIST_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS spell_whitelist_cache "
        "(user_notion_id TEXT PRIMARY KEY, terms_json TEXT, updated_at REAL)"
    )
    con.commit()
    return con


def _cache_get(user_notion_id: str) -> Optional[list[str]]:
    key = user_notion_id or "_anon"
    with _cache_db() as con:
        row = con.execute(
            "SELECT terms_json, updated_at FROM spell_whitelist_cache "
            "WHERE user_notion_id=?",
            (key,),
        ).fetchone()
    if not row:
        return None
    if time.time() - row[1] > _WHITELIST_TTL:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _cache_set(user_notion_id: str, terms: list[str]) -> None:
    key = user_notion_id or "_anon"
    with _cache_db() as con:
        con.execute(
            "INSERT OR REPLACE INTO spell_whitelist_cache "
            "(user_notion_id, terms_json, updated_at) VALUES (?,?,?)",
            (key, json.dumps(terms, ensure_ascii=False), time.time()),
        )


def invalidate_whitelist(user_notion_id: str = "") -> None:
    """Сброс кеша. Вызывать ПОСЛЕ create нового клиента/имени, иначе
    Haiku может «исправить» только что добавленное имя.
    """
    key = user_notion_id or "_anon"
    with _cache_db() as con:
        con.execute(
            "DELETE FROM spell_whitelist_cache WHERE user_notion_id=?",
            (key,),
        )


async def _fetch_client_names(user_notion_id: str) -> list:
    """Тянет имена клиентов из PG. Возвращает [] на ошибке."""
    try:
        from arcana.repos.pg_clients_repo import PgClientsRepo
        clients = await PgClientsRepo().list_all(user_notion_id)
        return [c.name for c in clients if c.name]
    except Exception as e:
        logger.warning("fetch client names failed: %s", e)
        return []


async def get_whitelist(user_notion_id: str = "") -> list[str]:
    cached = _cache_get(user_notion_id)
    if cached is not None:
        return cached
    client_names = await _fetch_client_names(user_notion_id)
    full = _static_whitelist() + client_names
    _cache_set(user_notion_id, full)
    return full


# ── Anti-conversational guard ────────────────────────────────────────────────

_CONVERSATIONAL_STARTS = (
    "я не", "извините", "к сожалению", "я имею", "я могу", "я не могу",
    "не имею", "у меня нет", "мне не", "как ии", "как ai",
    "вот", "конечно", "хорошо", "да,", "нет,",
)


def _looks_conversational(text: str) -> bool:
    low = text.lower().strip()
    return any(low.startswith(s) for s in _CONVERSATIONAL_STARTS)


def _too_long(corrected: str, original: str) -> bool:
    return len(corrected) > len(original) * 2 + 30


def _truncated(corrected: str, original: str) -> bool:
    """Haiku обрезала вывод (max_tokens). Защита от потери хвоста длинной
    сессии: если корректированный текст меньше 70% оригинала и оригинал
    длиннее 100 символов — отвергаем и возвращаем оригинал (см. issue #82).
    """
    if len(original) < 100:
        return False
    return len(corrected) < len(original) * 0.7


# ── Public API ───────────────────────────────────────────────────────────────

async def normalize_text(text: str, *, user_notion_id: str = "") -> str:
    """1) раскладка EN→RU 2) Haiku spell-correction с whitelist guard.

    Безопасно: на любую ошибку или подозрительный output Haiku возвращает
    оригинал.
    """
    if not text or not text.strip():
        return text
    converted = maybe_convert(text)
    if not converted.strip():
        return converted

    try:
        whitelist = await get_whitelist(user_notion_id)
    except Exception:
        whitelist = _static_whitelist()

    # Берём только те whitelist-термины, которые встречаются в тексте —
    # экономим токены промпта. case-insensitive.
    low = converted.lower()
    relevant = [w for w in whitelist if w.lower() in low]

    system_lines = [
        "Исправь опечатки и описки. Если нет ошибок — верни текст как есть.",
        "Только текст, без объяснений, без префиксов «Вот:», без кавычек.",
    ]
    if relevant:
        # Передаём только релевантную выборку — короче промпт.
        joined = ", ".join(relevant[:60])
        system_lines.append(
            f"НИКОГДА не «исправляй» эти слова (имена клиентов, "
            f"карты Таро, эзо-термины): {joined}."
        )
    system = "\n".join(system_lines)

    # Адаптивный max_tokens: длинные сессии (9+ триплетов, ~900 символов)
    # требуют ~600+ токенов на выход; статический лимит 200 резал текст
    # пополам (см. issue #82). Русский ≈ 1 токен на 2 символа.
    spell_max_tokens = max(300, len(converted) // 2 + 200)

    try:
        corrected = await ask_claude(
            converted, system=system, max_tokens=spell_max_tokens,
            model="claude-haiku-4-5-20251001",
        )
    except Exception as e:
        logger.warning("spell correction error: %s", e)
        return converted

    if not corrected:
        return converted
    c = corrected.strip().strip('"').strip("«»").strip()
    if not c:
        return converted
    if _looks_conversational(c):
        logger.warning("spell rejected (conversational): %r", c[:80])
        return converted
    if _too_long(c, converted):
        logger.warning("spell rejected (too long): %r", c[:80])
        return converted
    if _truncated(c, converted):
        logger.warning(
            "spell rejected (truncated %d→%d chars)", len(converted), len(c),
        )
        return converted
    return c
