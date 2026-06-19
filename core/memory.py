"""core/memory.py — общая логика долгосрочной памяти (Nexus + Arcana).

Storage: PG via core/repos/memory_repo.py (ADR-0005).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.config import config as _cfg
from core.layout import maybe_convert
from core.repos.memory_repo import _repo as _mem_repo
from core.repos.pg_memory_repo import Memory, bot_to_scope

logger = logging.getLogger("core.memory")

# Последние результаты поиска по памяти: uid → List[Memory]
_last_memory_results: Dict[int, List[Memory]] = {}

# Мульти-выбор удаления: страницы показанные в UI и выбранные юзером
_mem_delete_pages: Dict[int, List[Memory]] = {}
_mem_selected: Dict[int, Set[str]] = {}  # uid → set of memory_id (str)

# Точные значения категорий
CATEGORIES: List[str] = [
    "🦋 СДВГ", "👥 Люди", "🏥 Здоровье", "🛒 Предпочтения",
    "💼 Работа", "🏠 Быт", "🔄 Паттерн", "💡 Инсайт", "🔮 Практика", "🐾 Коты",
    "💰 Лимит", "🔒 Обязательные", "📥 Доход", "📋 Долги", "🎯 Цели",
]
_CATEGORIES_STR = " / ".join(CATEGORIES)

# ── Системный промпт для Haiku ─────────────────────────────────────────────────

_PARSE_SYSTEM = (
    "Ты парсишь факт для сохранения в долгосрочную память.\n"
    "Отвечай ТОЛЬКО валидным JSON без пояснений, без markdown:\n"
    '{"fact": "краткий факт одной строкой",\n'
    ' "category": "одна из категорий ниже",\n'
    ' "связь": "имя человека/кота/объекта или пустая строка",\n'
    ' "ключ": "snake_case_тег"}\n'
    "\n"
    f"Допустимые категории: {_CATEGORIES_STR}\n"
    "\n"
    "🦋 СДВГ — паттерны поведения, триггеры (что мешает фокусу), стратегии (что помогает), особенности памяти и восприятия\n"
    "\n"
    "Примеры:\n"
    '  "запомни что маша не ест мясо" → {"fact":"маша не ест мясо","category":"👥 Люди","связь":"маша","ключ":"маша_диета"}\n'
    '  "у меня аллергия на пыль" → {"fact":"аллергия на пыль","category":"🏥 Здоровье","связь":"","ключ":"аллергия"}\n'
    '  "батон весит 4 кг" → {"fact":"батон весит 4 кг","category":"🏠 Быт","связь":"батон","ключ":"батон"}\n'
    '  "я не ем сахар" → {"fact":"не ем сахар","category":"🛒 Предпочтения","связь":"","ключ":"диета_сахар"}\n'
    '  "маша это моя подруга" → {"fact":"маша — подруга","category":"👥 Люди","связь":"маша","ключ":"маша"}\n'
    '  "кот боится пылесоса" → {"fact":"боится пылесоса","category":"🐾 Коты","связь":"кот","ключ":"кот_страх"}\n'
    '  "у меня дислексия" → {"fact":"дислексия","category":"🦋 СДВГ","связь":"","ключ":"дислексия"}\n'
    '  "я быстро забываю книги и сериалы" → {"fact":"быстро забывает книги и сериалы, помнит только впечатление","category":"🦋 СДВГ","связь":"","ключ":"паттерн_память_контент"}\n'
    '  "если вещь не на виду — её не существует" → {"fact":"если вещь не на виду — её не существует","category":"🦋 СДВГ","связь":"","ключ":"паттерн_видимость_вещей"}\n'
    '  "в гиперфокусе нельзя отвлекать" → {"fact":"в гиперфокусе нельзя отвлекать","category":"🦋 СДВГ","связь":"","ключ":"паттерн_гиперфокус"}\n'
    '  "когда взволнована — кладу вещи неосознанно и теряю" → {"fact":"в возбуждении кладёт вещи неосознанно, потом не может найти","category":"🦋 СДВГ","связь":"","ключ":"паттерн_тревога_вещи"}\n'
    '  "белый шум мешает" → {"fact":"белый шум мешает концентрации","category":"🦋 СДВГ","связь":"","ключ":"триггер_белый_шум"}\n'
    '  "нужен фон — музыка или видос, но без лишних шумов сверху" → {"fact":"нужен один фоновый звук, лишние шумы сверху мешают","category":"🦋 СДВГ","связь":"","ключ":"триггер_звуки"}\n'
    '  "помогают сдвг кольца" → {"fact":"СДВГ-кольца помогают с фокусом","category":"🦋 СДВГ","связь":"","ключ":"стратегия_кольца"}\n'
    '  "витамины помогают при сдвг" → {"fact":"витамины помогают","category":"🦋 СДВГ","связь":"","ключ":"стратегия_витамины"}\n'
    '  "если плохо сплю или не ем — становлюсь злой" → {"fact":"плохой сон или еда → раздражительность","category":"🦋 СДВГ","связь":"","ключ":"триггер_сон_еда"}\n'
    '  "royal canin indoor 2кг" → {"fact":"royal canin indoor 2кг","category":"🐾 Коты","связь":"коты","ключ":"royal_canin"}\n'
    '  "алуна не ест курицу" → {"fact":"алуна не ест курицу","category":"🐾 Коты","связь":"алуна","ключ":"алуна_еда"}\n'
    '  "лимит на сигареты 3000р в месяц" → {"fact":"лимит: 🚬 Привычки — 3000₽/мес","category":"💰 Лимит","связь":"привычки","ключ":"лимит_привычки"}\n'
    '  "поставь лимит на кафе 5000р" → {"fact":"лимит: 🍱 Кафе/Доставка — 5000₽/мес","category":"💰 Лимит","связь":"кафе","ключ":"лимит_кафе"}\n'
    '  "лимит на продукты 8000р" → {"fact":"лимит: 🍜 Продукты — 8000₽/мес","category":"💰 Лимит","связь":"продукты","ключ":"лимит_продукты"}\n'
    "\n"
    "БЮДЖЕТ — обязательные расходы, цели, долги (все в категории 💰 Лимит):\n"
    '  "обязательный расход квартира 25000" → {"fact":"обязательно: 🏠 Жильё — 25000₽/мес","category":"💰 Лимит","связь":"ж***","ключ":"обязательно_ж***"}\n'
    '  "обязательный расход подписки 10700" → {"fact":"обязательно: 💻 Подписки — 10700₽/мес","category":"💰 Лимит","связь":"подписки","ключ":"обязательно_подписки"}\n'
    '  "обязательный расход коты 10000" → {"fact":"обязательно: 🐾 Коты — 10000₽/мес","category":"💰 Лимит","связь":"коты","ключ":"обязательно_коты"}\n'
    '  "обязательный расход привычки 17500" → {"fact":"обязательно: 🚬 Привычки — 17500₽/мес","category":"💰 Лимит","связь":"привычки","ключ":"обязательно_привычки"}\n'
    '  "обязательный расход бьюти 3500" → {"fact":"обязательно: 💅 Бьюти — 3500₽/мес","category":"💰 Лимит","связь":"бьюти","ключ":"обязательно_бьюти"}\n'
    '  "обязательный расход проезд 3000" → {"fact":"обязательно: 🚕 Проезд — 3000₽/мес","category":"💰 Лимит","связь":"проезд","ключ":"обязательно_проезд"}\n'
    '  "обязательный расход вода 2500" → {"fact":"обязательно: 💧 Вода — 2500₽/мес","category":"💰 Лимит","связь":"вода","ключ":"обязательно_вода"}\n'
    '  "цель телефон 100000" → {"fact":"цель: 📱 Телефон — 100000₽ · откладываю 0₽/мес","category":"💰 Лимит","связь":"телефон","ключ":"цель_телефон"}\n'
    '  "цель ПК 200000" → {"fact":"цель: 💻 ПК — 200000₽ · откладываю 0₽/мес","category":"💰 Лимит","связь":"пк","ключ":"цель_пк"}\n'
    '  "цель подушка 100000" → {"fact":"цель: 💰 Подушка — 100000₽ · откладываю 0₽/мес","category":"💰 Лимит","связь":"подушка","ключ":"цель_подушка"}\n'
    '  "долг подружке 50000 до апреля" → {"fact":"долг: 👩 Подружка — 50000₽ · дедлайн: апрель 2026","category":"💰 Лимит","связь":"подружка","ключ":"долг_подружка"}\n'
    '  "долг другу 40000" → {"fact":"долг: 👤 Друг — 40000₽","category":"💰 Лимит","связь":"друг","ключ":"долг_друг"}\n'
    '  "убери обязательный расход интернет" → {"fact":"обязательно: интернет — 0₽/мес","category":"💰 Лимит","связь":"интернет","ключ":"обязательно_интернет"}\n'
    '  "измени обязательный квартира на 26000" → {"fact":"обязательно: 🏠 Жильё — 26000₽/мес","category":"💰 Лимит","связь":"ж***","ключ":"обязательно_ж***"}\n'
    "ВАЖНО: ключ для обязательных ВСЕГДА начинается с 'обязательно_', для целей — 'цель_', для долгов — 'долг_', для лимитов — 'лимит_'.\n"
    "ВАЖНО: 'убери обязательный расход X' → запиши fact с суммой 0 (будет обновлена существующая запись, а сумма 0 = деактивация).\n"
    "ВАЖНО: 'измени обязательный X на Y' → запиши fact с новой суммой (будет обновлена существующая запись)."
)

_STRIP_RE = re.compile(r"^\s*запомни\s+(что\s+)?", re.IGNORECASE)


# ── Парсинг факта через Haiku ──────────────────────────────────────────────────

async def _parse_fact(text: str) -> Tuple[str, str, str, str]:
    """Возвращает (fact, category, связь, ключ)."""
    try:
        raw = await ask_claude(
            text,
            system=_PARSE_SYSTEM,
            max_tokens=200,
            model="claude-haiku-4-5-20251001",
            temperature=0,
        )
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
        fact     = (parsed.get("fact")     or "").strip()
        category = (parsed.get("category") or "").strip()
        связь    = (parsed.get("связь")    or "").strip()
        ключ     = (parsed.get("ключ")     or "").strip()
        if fact and ключ:
            if category not in CATEGORIES:
                category = "💡 Инсайт"
            return fact, category, связь, ключ
    except Exception as e:
        logger.error("memory _parse_fact error: %s", e)

    fact = _STRIP_RE.sub("", text).strip() or text
    return fact, "💡 Инсайт", "", "факт"


# ── Поиск ──────────────────────────────────────────────────────────────────────

_SEARCH_STOP = {"про", "о", "об", "и", "не", "это", "что", "как", "из", "по",
                "для", "на", "в", "с", "к", "у", "за", "от"}


def _normalize_word(word: str) -> str:
    """Убрать падежные окончания для поиска contains. Минимальная основа — 3 символа."""
    for suffix in ("ами", "ями", "ого", "его", "ому", "ему", "ой", "ей",
                   "ом", "ем", "ах", "ях", "ам", "ям", "ую", "юю",
                   "ов", "ев", "ёв", "ий", "ый", "ая", "яя",
                   "у", "ю", "а", "я", "е", "и", "ы", "о"):
        if word.endswith(suffix):
            stem = word[:-len(suffix)]
            if len(stem) >= 3:
                return stem
    return word


def _tokenize_hint(hint: str) -> List[str]:
    """Разбить hint на нормализованные токены, отфильтровав стоп-слова."""
    tokens = []
    for w in hint.lower().split():
        w = w.strip(".,!?;:«»\"'")
        if len(w) >= 2 and w not in _SEARCH_STOP:
            tokens.append(_normalize_word(w))
    return tokens


async def _find_pages_by_hint(hint: str, page_size: int = 10) -> List[Memory]:
    """Умный поиск по hint через PG. Возвращает List[Memory].

    Функция оставлена module-level: тесты test_memory_aliases патчат её.
    """
    if not hint or not hint.strip():
        return []

    _CAT_MAP = {
        "сдвг": "🦋 СДВГ", "люди": "👥 Люди", "здоровье": "🏥 Здоровье",
        "предпочтения": "🛒 Предпочтения", "работа": "💼 Работа", "быт": "🏠 Быт",
        "паттерн": "🔄 Паттерн", "инсайт": "💡 Инсайт", "практика": "🔮 Практика",
        "коты": "🐾 Коты", "лимит": "💰 Лимит",
    }
    hint_lower = hint.lower().strip()
    if hint_lower in _CAT_MAP:
        matched_cat = _CAT_MAP[hint_lower]
        logger.info("_find_pages_by_hint: category shortcut → %s", matched_cat)
        try:
            results = await _mem_repo.find_by_category(matched_cat, is_current=True, page_size=100)
            logger.info("_find_pages_by_hint category shortcut: found=%d", len(results))
            return results
        except Exception as e:
            logger.error("_find_pages_by_hint category shortcut error: %s", e)

    tokens = _tokenize_hint(hint)
    logger.info("memory _find_pages_by_hint: hint=%r tokens=%s", hint, tokens)

    search_terms = tokens if tokens else [hint.strip()]
    try:
        results = await _mem_repo.search(search_terms, page_size=page_size)
        logger.info("_find_pages_by_hint: found=%d for terms=%s", len(results), search_terms)
        return results
    except Exception as e:
        logger.error("memory _find_pages_by_hint: %s", e, exc_info=True)
        return []


# ── v1.2.4: alias resolver ───────────────────────────────────────────────────

_ALIAS_PATTERNS = [
    re.compile(
        r"у\s+([\w\-]+)\s+(?:краткая\s+|короткая\s+|сокращ\w+\s+)?"
        r"(?:кличк\w*|прозвищ\w*|погоняло|никнейм)\s+([\w\-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"у\s+([\w\-]+)\s+также\s+"
        r"(?:называется\s+|известн\w+\s+как\s+)?([\w\-]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([\w\-]+)\s*\(\s*(?:он|она|оно)\s+же\s+([\w\-]+)\s*\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([\w\-]+)\s+(?:коротко|сокращ\w+)\s+([\w\-]+)",
        re.IGNORECASE,
    ),
    re.compile(r"([\w\-]+)\s*[=—]\s*([\w\-]+)\b", re.IGNORECASE),
]

_ALIAS_DEPTH_LIMIT = 3


async def _resolve_alias(
    связь: str,
    user_notion_id: str = "",
    _depth: int = 0,
    _seen: Optional[Set[str]] = None,
) -> str:
    """Канонизировать связь через existing memories."""
    if not связь or not связь.strip():
        return связь
    if _depth >= _ALIAS_DEPTH_LIMIT:
        logger.info("memory: alias resolver depth limit (%d) reached at %r",
                    _ALIAS_DEPTH_LIMIT, связь)
        return связь

    seen = _seen if _seen is not None else set()
    link_lower = связь.lower().strip()
    if link_lower in seen:
        logger.info("memory: alias cycle detected at %r, stopping", связь)
        return связь
    seen = seen | {link_lower}

    try:
        mems = await _find_pages_by_hint(связь)
    except Exception as e:
        logger.warning("memory _resolve_alias: _find_pages_by_hint failed for %r: %s",
                       связь, e)
        return связь

    if not mems:
        return связь

    for m in mems:
        fact_text = (m.fact or "").lower()
        if not fact_text or fact_text == "—":
            continue

        for pattern in _ALIAS_PATTERNS:
            for match in pattern.finditer(fact_text):
                primary = match.group(1).strip().lower()
                alias = match.group(2).strip().lower()
                if not primary or not alias:
                    continue
                if alias == link_lower and primary != link_lower:
                    logger.info(
                        "memory: alias resolved %r → %r (matched: %r)",
                        связь, primary, match.group(0),
                    )
                    return await _resolve_alias(
                        primary, user_notion_id,
                        _depth=_depth + 1, _seen=seen,
                    )

    return связь


# ── Public API ──────────────────────────────────────────────────────────────────

_ADHD_TIP_SYSTEM = """Ты знаешь конкретного человека с СДВГ. Её зовут Кай, она — женщина. Обращайся к ней по имени, используй женский род. Вот её профиль:
- Быстро забывает контент (книги, сериалы) — помнит только впечатление
- Если вещь не на виду — её не существует
- В гиперфокусе нельзя отвлекать
- В тревоге кладёт вещи неосознанно и теряет
- Белый шум и лишние звуки мешают
- Нужен один фоновый звук (музыка/видос)
- Помогают СДВГ-кольца, витамины, коты, режим дня
- Плохой сон или еда = раздражительность
- Прокрастинация и руминация — сильные
- Помогают будильники, списки, визуальный порядок
- Вещи должны лежать на своих местах всегда
- Утро начинается с энергетика Monster и сигареты Chapman Green
- Сова, но лучше живёт при солнечном свете
Человек только что записал в память факт про себя (категория СДВГ).
Дай ОДИН конкретный, не банальный совет именно под этот факт и этого человека.
Учитывай профиль — не советуй то, что уже делает.
Совет = 1-2 предложения максимум. Начни с эмодзи. Без вступлений."""


async def _get_adhd_tip(fact: str) -> str:
    tip = await ask_claude(
        fact,
        system=_ADHD_TIP_SYSTEM,
        max_tokens=150,
        model=_cfg.model_sonnet,
        temperature=0.7,
    )
    return tip.strip()


async def save_memory(
    message: Message,
    text: str,
    user_notion_id: str,
    bot_label: str,
) -> None:
    """Распарсить текст через Haiku и сохранить факт в PG."""
    text = maybe_convert(text.strip())
    logger.info("memory save: text=%r bot=%s", text[:60], bot_label)

    fact, category, связь, ключ = await _parse_fact(text)
    scope = bot_to_scope(bot_label)

    if category != "💰 Лимит" and связь:
        original_link = связь
        canonical_link = await _resolve_alias(связь, user_notion_id)
        if canonical_link and canonical_link != original_link:
            связь = canonical_link
            old_key = ключ
            orig_lower = original_link.lower()
            if old_key.lower().startswith(orig_lower + "_"):
                ключ = canonical_link.lower() + old_key[len(original_link):]
            elif old_key.lower() == orig_lower:
                ключ = canonical_link.lower()
            logger.info(
                "memory: canonicalized link %r→%r, key %r→%r",
                original_link, canonical_link, old_key, ключ,
            )

    logger.info("memory save: writing to PG fact=%r key=%s cat=%s", fact, ключ, category)
    try:
        was_updated = False
        if category == "💰 Лимит" and ключ:
            mid, was_updated = await _mem_repo.upsert(
                fact, ключ, category, scope, связь, "manual", user_notion_id
            )
            result = mid
        else:
            result = await _mem_repo.add(
                fact, ключ, category, scope, связь, "manual", user_notion_id
            )

        if result:
            logger.info("memory save: %s id=%s", "updated" if was_updated else "created", result)
            if was_updated:
                if ключ.startswith("обязательно_"):
                    if "0₽" in fact or "— 0" in fact:
                        await message.answer(f"🧠 Убрал обязательный расход: {связь or ключ}")
                    else:
                        await message.answer(f"🧠 Обновил обязательный расход: {fact}")
                elif ключ.startswith("цель_"):
                    await message.answer(f"🧠 Обновил цель: {fact}")
                elif ключ.startswith("долг_"):
                    await message.answer(f"🧠 Обновил долг: {fact}")
                else:
                    await message.answer(f"🧠 Обновил лимит: {fact}")
            else:
                if ключ.startswith("обязательно_"):
                    await message.answer(f"📌 Добавил обязательный расход: {fact}")
                elif ключ.startswith("цель_"):
                    await message.answer(f"🎯 Добавил цель: {fact}")
                elif ключ.startswith("долг_"):
                    await message.answer(f"📋 Добавил долг: {fact}")
                else:
                    cat_label = f" [{category}]" if category else ""
                    await message.answer(f"🧠 Запомнил{cat_label}: {fact}")
                if category == "🦋 СДВГ":
                    try:
                        tip = await _get_adhd_tip(fact)
                        if tip:
                            await message.answer(tip)
                    except Exception as e:
                        logger.debug("adhd tip error: %s", e)
        else:
            logger.error("memory save: repo returned None")
            await message.answer("⚠️ Ошибка записи в базу")
    except Exception as e:
        logger.error("memory save: error %s", e)
        await message.answer(f"⚠️ Ошибка записи: {e}")


async def _search_finance(query: str, page_size: int = 5) -> list:
    """Поиск по базе финансов PG (nexus_budget)."""
    if not query:
        return []
    try:
        from core.repos.pg_finance_repo import PgNexusBudgetRepo
        repo = PgNexusBudgetRepo()
        return await repo.search_description(query, page_size=page_size)
    except Exception as e:
        logger.error("memory search_finance: %s", e)
        return []


async def search_memory(
    message: Message,
    query: str,
    user_notion_id: str,
    del_prefix: str = "mem_del",
) -> None:
    """Поиск по памяти + финансам параллельно."""
    query = query.strip()

    if query:
        tokens = _tokenize_hint(query) or [query]
        mem_coro = _mem_repo.search(tokens, page_size=10)
        fin_coro = _search_finance(query, page_size=5)
        mems, fin_pages = await asyncio.gather(mem_coro, fin_coro)
        logger.info("memory search: hint=%r mems=%d fin=%d",
                    query, len(mems), len(fin_pages))
    else:
        try:
            mems = await _mem_repo.find_recent(is_current=True, page_size=10)
        except Exception as e:
            logger.error("memory search: %s", e)
            mems = []
        fin_pages = []

    if not mems and not fin_pages:
        suffix = f" по «{query}»" if query else ""
        await message.answer(f"🧠 Ничего не нашёл в памяти{suffix}")
        return

    uid = message.from_user.id
    _last_memory_results[uid] = list(mems)
    _mem_delete_pages[uid] = list(mems)
    _mem_selected[uid] = set()

    parts: List[str] = []

    # ── Память ──
    if mems:
        lines = []
        try:
            adhd_mems = [m for m in mems if m.category == "🦋 СДВГ"]
            other_mems = [m for m in mems if m.category != "🦋 СДВГ"]

            if adhd_mems:
                _ADHD_GROUPS = [
                    ("🔄 Паттерны", ["паттерн", "забыва", "теря", "откладыва", "не существует", "неосознанно"]),
                    ("💡 Стратегии", ["помога", "стратеги", "лучше", "кольц", "витамин", "таймер"]),
                    ("⚡ Триггеры", ["мешает", "триггер", "хуже", "не могу", "белый", "шум", "раздраж"]),
                ]
                grouped: dict = {}
                for m in adhd_mems:
                    low = m.fact.lower()
                    placed = False
                    for group_name, keywords in _ADHD_GROUPS:
                        if any(kw in low for kw in keywords):
                            grouped.setdefault(group_name, []).append(m.fact)
                            placed = True
                            break
                    if not placed:
                        grouped.setdefault("📌 Особенности", []).append(m.fact)

                adhd_lines = ["🧠 <b>СДВГ:</b>"]
                for group_name in ["🔄 Паттерны", "💡 Стратегии", "⚡ Триггеры", "📌 Особенности"]:
                    items = grouped.get(group_name, [])
                    if items:
                        adhd_lines.append(f"  <b>{group_name}:</b>")
                        for item in items:
                            adhd_lines.append(f"    • {item}")
                lines.append("\n".join(adhd_lines))

            for m in other_mems:
                cat_emoji = m.category.split(" ")[0] if m.category else "💡"
                inactive_mark = " <i>(неактуально)</i>" if not m.is_current else ""
                line2 = f"<i>{m.category} · {m.date}</i>" if m.category else f"<i>{m.date}</i>"
                lines.append(f"{cat_emoji} {m.fact}{inactive_mark}\n{line2}")
        except Exception as e:
            logger.error("search_memory formatting error: %s", e, exc_info=True)
            lines = []
            for m in mems:
                cat_emoji = m.category.split(" ")[0] if m.category else "💡"
                inactive_mark = " <i>(неактуально)</i>" if not m.is_current else ""
                line2 = f"<i>{m.category} · {m.date}</i>" if m.category else f"<i>{m.date}</i>"
                lines.append(f"{cat_emoji} {m.fact}{inactive_mark}\n{line2}")

        all_cats = set(m.category for m in mems)
        if len(all_cats) == 1:
            single_cat = all_cats.pop()
            header = f"{single_cat} ({len(mems)} зап.)"
        else:
            header = f"🧠 <b>Память</b> (найдено {len(mems)})"
        parts.append(f"{header}:\n\n" + "\n\n".join(lines))

    # ── Финансы ──
    if fin_pages:
        try:
            fin_lines = []
            for entry in fin_pages:
                desc = entry.description or "—"
                amount = entry.amount or ""
                date_str = (entry.date or "")[:10]
                amount_str = f"{amount:g}₽" if amount else ""
                fin_lines.append(f"· {desc} {amount_str} · {date_str}".strip())
            parts.append("💰 <b>Финансы:</b>\n" + "\n".join(fin_lines))
        except Exception as e:
            logger.error("search_memory finance formatting error: %s", e, exc_info=True)

    text = "\n\n".join(parts)
    if not text.strip():
        suffix = f" по «{query}»" if query else ""
        await message.answer(f"🧠 Ничего не нашёл в памяти{suffix}")
        return
    kb = _build_delete_keyboard(uid, list(mems), reactivate_cb="mem_reactivate_selected") if mems else None
    await message.answer(text, reply_markup=kb)


async def deactivate_memory(
    message: Message,
    hint: str,
    user_notion_id: str,
) -> None:
    """Пометить запись памяти как неактуальную."""
    uid = message.from_user.id
    last = _last_memory_results.get(uid, [])

    if not hint or hint.lower() == "все":
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        mems = last
    elif hint.isdigit():
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        idx = int(hint) - 1
        if not (0 <= idx < len(last)):
            await message.answer(f"🧠 Записи №{hint} нет в результатах поиска (всего {len(last)})")
            return
        mems = [last[idx]]
    else:
        mems = await _find_pages_by_hint(hint) if hint else []
        if not mems:
            tokens = _tokenize_hint(hint)
            subject = tokens[0] if tokens else hint
            await message.answer(f"🧠 Не нашёл записей о <b>{subject}</b>")
            return

    try:
        await _mem_repo.set_active([m.id for m in mems], False)
        facts = ", ".join(f"<b>{m.fact}</b>" for m in mems)
        await message.answer(f"🧠 Помечено как неактуальное: {facts}")
    except Exception as e:
        logger.error("memory deactivate: %s", e)
        await message.answer("⚠️ Ошибка обновления")


def _build_delete_keyboard(
    uid: int,
    pages: List[Memory],
    toggle_prefix: str = "mem_toggle",
    selected_cb: str = "mem_deactivate_selected",
    selected_label: str = "☑️ Отметить неактуальными",
    all_cb: str = "mem_deactivate_all",
    all_label: str = "☑️ Отметить все неактуальными",
    cancel_label: str = "❌ Закрыть",
    reactivate_cb: str = "",
    reactivate_label: str = "↩️ Восстановить выбранные",
) -> InlineKeyboardMarkup:
    """Клавиатура чекбоксов для записей памяти. Принимает List[Memory]."""
    selected = _mem_selected.get(uid, set())
    n_selected = len(selected)
    buttons = []
    for m in pages:
        mid = m.id
        is_inactive = not m.is_current
        icon = "✅" if mid in selected else "☐"
        label = f"{icon} {m.fact[:40]}" + (" ·· неакт." if is_inactive else "")
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"{toggle_prefix}:{mid}",
        )])
    n_inactive = sum(1 for m in pages if not m.is_current)
    n_active = len(pages) - n_inactive

    if n_selected:
        selected_active = any(m.is_current for m in pages if m.id in selected)
        if selected_active:
            buttons.append([InlineKeyboardButton(
                text=f"{selected_label} ({n_selected})",
                callback_data=f"{selected_cb}:{uid}",
            )])
        if reactivate_cb:
            buttons.append([InlineKeyboardButton(
                text=f"{reactivate_label} ({n_selected})",
                callback_data=f"{reactivate_cb}:{uid}",
            )])
    if n_active:
        buttons.append([InlineKeyboardButton(
            text=f"{all_label} ({n_active})",
            callback_data=f"{all_cb}:{uid}",
        )])
    if reactivate_cb and n_inactive:
        buttons.append([InlineKeyboardButton(
            text=f"↩️ Восстановить все ({n_inactive})",
            callback_data=f"mem_reactivate_all:{uid}",
        )])
    buttons.append([InlineKeyboardButton(
        text=cancel_label,
        callback_data=f"mem_cancel:{uid}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def delete_memory(
    message: Message,
    hint: str,
    user_notion_id: str,
    del_prefix: str = "mem_del",
    cancel_cb: str = "mem_cancel",
) -> None:
    """Удалить (архивировать) запись памяти."""
    uid = message.from_user.id
    last = _last_memory_results.get(uid, [])

    if not hint or hint.lower() == "все":
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        mems = last
    elif hint.isdigit():
        if not last:
            await message.answer("🧠 Сначала найди записи — например: «напомни про машу»")
            return
        idx = int(hint) - 1
        if not (0 <= idx < len(last)):
            await message.answer(f"🧠 Записи №{hint} нет в результатах поиска (всего {len(last)})")
            return
        mems = [last[idx]]
    else:
        mems = await _find_pages_by_hint(hint) if hint else []
        if not mems:
            tokens = _tokenize_hint(hint)
            subject = tokens[0] if tokens else hint
            await message.answer(f"🧠 Не нашёл записей о <b>{subject}</b>")
            return

    if len(mems) == 1:
        await _mem_repo.archive(mems[0].id)
        _last_memory_results[uid] = [m for m in last if m.id != mems[0].id]
        await message.answer(f"🗑 Удалено из памяти: <b>{mems[0].fact}</b>")
        return

    shown = mems[:10]
    _mem_delete_pages[uid] = shown
    _mem_selected[uid] = set()
    await message.answer(
        "🧠 Выбери записи для удаления:",
        reply_markup=_build_delete_keyboard(
            uid, shown,
            toggle_prefix="mem_del_toggle",
            selected_cb="mem_delete_selected",
            selected_label="🗑️ Удалить выбранные",
            all_cb="mem_delete_all",
            all_label="🗑️ Удалить все",
            cancel_label="❌ Отмена",
        ),
    )


async def recall_from_memory(keyword: str) -> Optional[str]:
    """Ищет в памяти факт по ключевому слову. Возвращает текст первого совпадения или None."""
    if not keyword or not keyword.strip():
        return None
    norm = _normalize_word(keyword.lower().strip())
    if len(norm) < 2:
        return None
    results = await _mem_repo.search([norm], page_size=3)
    if not results:
        results = await _mem_repo.search([keyword.strip()], page_size=3)
    for m in results:
        if m.is_current and m.fact:
            return m.fact
    return None


def extract_context_keywords(data: dict, client_name: Optional[str] = None) -> List[str]:
    """Извлекает ключевые слова для поиска в памяти из распарсенных данных."""
    keywords: List[str] = []
    if client_name:
        keywords.append(client_name)
    for key in ("spread_type", "deck", "category", "goal", "place", "name"):
        val = data.get(key)
        if val and isinstance(val, str) and len(val) > 2:
            keywords.append(val)
    return keywords


async def get_memories_for_context(
    user_notion_id: str,
    keywords: List[str],
    bot_label: str = "🌒 Arcana",
    max_results: int = 10,
) -> str:
    """Подтягивает релевантные записи из Памяти для вставки в промпт."""
    if not keywords:
        return ""
    scope = bot_to_scope(bot_label)
    try:
        seen_ids: Set[str] = set()
        mems: List[Memory] = []
        for kw in keywords:
            if not kw or len(kw) < 2:
                continue
            found = await _find_pages_by_hint(kw, page_size=max_results)
            for m in found:
                if m.id in seen_ids:
                    continue
                # Фильтр по scope: оставить если scope совпадает ИЛИ global
                if m.scope and m.scope != "global" and m.scope != scope:
                    continue
                seen_ids.add(m.id)
                mems.append(m)
                if len(mems) >= max_results:
                    break
            if len(mems) >= max_results:
                break

        if not mems:
            return ""

        lines = ["Контекст из памяти:"]
        for m in mems:
            cat_str = f" [{m.category}]" if m.category else ""
            lines.append(f"- {m.fact[:200]}{cat_str}")

        return "\n".join(lines)
    except Exception:
        logger.warning("get_memories_for_context failed, continuing without context")
        return ""


async def auto_suggest_memory(
    message: Message,
    text: str,
    user_notion_id: str,
    bot_label: str,
    pending_store: Dict[int, dict],
    yes_prefix: str = "mem_auto_yes",
    no_prefix: str  = "mem_auto_no",
) -> None:
    """Предложить сохранить факт в память (inline да/нет)."""
    if not text or not text.strip() or text.strip() in ("()", "(  )"):
        return
    uid = message.from_user.id
    pending_store[uid] = {"text": text, "user_notion_id": user_notion_id}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧠 Да, запомнить", callback_data=f"{yes_prefix}:{uid}"),
        InlineKeyboardButton(text="✗ Нет",            callback_data=f"{no_prefix}:{uid}"),
    ]])
    await message.answer(
        f"💡 Сохранить в память?\n<i>{text[:100]}</i>",
        reply_markup=kb,
    )
