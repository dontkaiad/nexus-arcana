"""core/memory.py — общая логика долгосрочной памяти (Nexus + Arcana).

Storage: PG via core/repos/memory_repo.py (ADR-0005).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from typing import Dict, List, Optional, Set, Tuple

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.claude_client import ask_claude
from core.config import config as _cfg
from core.layout import maybe_convert
from core.people import owner_fact
from core import memory_rag as _memory_rag
from core import memory_answer as _memory_answer
from core import memory_search as _memory_search
from core import pending_kv as _pkv
from core.repos.memory_repo import _repo as _mem_repo
from core.repos.pg_memory_repo import Memory, bot_to_scope
from core.ru_morph import strip_case_ending as _normalize_word  # #136: вынесено в core/ru_morph

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
    "💰 Лимит", "🔒 Постоянные", "📥 Доход", "📋 Долги", "🎯 Цели", "🎭 Фигуранты",
]

# #85: категория 🎭 Фигуранты — словарь «кодовое слово → кто/что это».
# Кай прячет имена фигурантов раскладов за нарицательными кличками
# («корабль», «дом»), чтобы не светить их в переписке с ботом. Arcana-парсер
# сессий (arcana/handlers/sessions.py) подтягивает ТОЛЬКО эту категорию в
# промпт как словарь сущностей — см. get_figurant_dictionary().
FIGURANT_CATEGORY = "🎭 Фигуранты"
_CATEGORIES_STR = " / ".join(CATEGORIES)

# ── Системный промпт для Haiku ─────────────────────────────────────────────────

_PARSE_SYSTEM = (
    "Ты парсишь факт для сохранения в долгосрочную память.\n"
    "Отвечай ТОЛЬКО валидным JSON без пояснений, без markdown — ВСЕГДА один "
    "объект, НЕ массив (несколько фактов — склей в один fact):\n"
    '{"fact": "краткий факт одной строкой",\n'
    ' "category": "одна из категорий ниже",\n'
    ' "связь": "имя человека/кота/объекта или пустая строка",\n'
    ' "ключ": "snake_case_тег"}\n'
    "\n"
    f"Допустимые категории: {_CATEGORIES_STR}\n"
    "\n"
    "🦋 СДВГ — паттерны поведения, триггеры (что мешает фокусу), стратегии (что помогает), особенности памяти и восприятия\n"
    "🔄 Паттерн — общие поведенческие привычки и повторяющиеся реакции, "
    "НЕ связанные со спецификой СДВГ: не хронотип, не фокус/триггеры, не "
    "память/восприятие (это остаётся в 🦋 СДВГ) — сюда идут привычки в "
    "принятии решений, откладывание конкретных дел, реакции в отношениях "
    "и похожие повторяющиеся модели поведения без явной СДВГ-причины\n"
    "🎭 Фигуранты — словарь «кодовое слово → кто/что это». Нарицательное "
    "слово (корабль, дом, лодка), которым пользователь заменяет реального "
    "человека/объект в раскладах таро, чтобы не писать имя. Триггеры: "
    "«X это Y», «X — кодовое слово для Y», «под X имею в виду Y», «X = "
    "объект/фигурант ...». ключ = само кодовое слово (snake_case), "
    "связь = кодовое слово.\n"
    "\n"
    "Примеры:\n"
    '  "корабль это объект приворота клиентки оли, реально пётр" → {"fact":"корабль = объект приворота клиентки Оли, реальное имя Пётр","category":"🎭 Фигуранты","связь":"корабль","ключ":"корабль"}\n'
    '  "под домом в раскладах имею в виду квартиру мамы" → {"fact":"дом = квартира мамы (в раскладах)","category":"🎭 Фигуранты","связь":"дом","ключ":"дом"}\n'
    '  "запомни что маша не ест мясо" → {"fact":"маша не ест мясо","category":"👥 Люди","связь":"маша","ключ":"маша_диета"}\n'
    '  "у меня аллергия на пыль" → {"fact":"аллергия на пыль","category":"🏥 Здоровье","связь":"","ключ":"аллергия"}\n'
    '  "батон весит 4 кг" → {"fact":"батон весит 4 кг","category":"🏠 Быт","связь":"батон","ключ":"батон"}\n'
    '  "у Хена др 17 октября 2004" → {"fact":"у Хена день рождения 17 октября 2004","category":"👥 Люди","связь":"хен","ключ":"хен_день_рождения"}\n'
    "ВАЖНО: если в тексте есть имя человека/кота — оно ОБЯЗАТЕЛЬНО остаётся в fact (не «день рождения 17 октября», а «у Хена день рождения 17 октября»). Факт должен быть понятен без исходного сообщения.\n"
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
    "\n"
    "Отличие 🔄 Паттерн от 🦋 СДВГ — тот же тип факта (повторяющаяся "
    "привычка/реакция), но БЕЗ связи с фокусом/памятью/сенсорикой:\n"
    '  "перед крупными покупками я долго думаю и сравниваю варианты" → {"fact":"перед крупной покупкой долго сравнивает варианты, прежде чем решиться","category":"🔄 Паттерн","связь":"","ключ":"паттерн_решение_покупки"}\n'
    '  "после ссоры мне нужно несколько дней чтобы написать первой" → {"fact":"после конфликта берёт паузу на несколько дней, прежде чем написать первой","category":"🔄 Паттерн","связь":"","ключ":"паттерн_пауза_после_конфликта"}\n'
    '  "в отношениях всегда первая иду мириться, даже если не виновата" → {"fact":"в конфликте первой идёт мириться, даже если не виновата","category":"🔄 Паттерн","связь":"","ключ":"паттерн_первая_мирится"}\n'
    '  "предпочитаю решать конфликт лично, а не в переписке" → {"fact":"предпочитает решать конфликт при личном разговоре, не в переписке","category":"🔄 Паттерн","связь":"","ключ":"паттерн_конфликт_лично"}\n'
    '  "royal canin indoor 2кг" → {"fact":"royal canin indoor 2кг","category":"🐾 Коты","связь":"коты","ключ":"royal_canin"}\n'
    '  "алуна не ест курицу" → {"fact":"алуна не ест курицу","category":"🐾 Коты","связь":"алуна","ключ":"алуна_еда"}\n'
    '  "лимит на сигареты 3000р в месяц" → {"fact":"лимит: 🚬 Привычки — 3000₽/мес","category":"💰 Лимит","связь":"привычки","ключ":"лимит_привычки"}\n'
    '  "поставь лимит на кафе 5000р" → {"fact":"лимит: 🍱 Кафе/Доставка — 5000₽/мес","category":"💰 Лимит","связь":"кафе","ключ":"лимит_кафе"}\n'
    '  "лимит на продукты 8000р" → {"fact":"лимит: 🍜 Продукты — 8000₽/мес","category":"💰 Лимит","связь":"продукты","ключ":"лимит_продукты"}\n'
    "\n"
    "БЮДЖЕТ — постоянные расходы, цели, долги (все в категории 💰 Лимит):\n"
    '  "постоянный расход квартира 25000" → {"fact":"постоянно: 🏠 Жильё — 25000₽/мес","category":"💰 Лимит","связь":"ж***","ключ":"постоянно_ж***"}\n'
    '  "постоянный расход подписки 10700" → {"fact":"постоянно: 💻 Подписки — 10700₽/мес","category":"💰 Лимит","связь":"подписки","ключ":"постоянно_подписки"}\n'
    '  "постоянный расход коты 10000" → {"fact":"постоянно: 🐾 Коты — 10000₽/мес","category":"💰 Лимит","связь":"коты","ключ":"постоянно_коты"}\n'
    '  "постоянный расход привычки 17500" → {"fact":"постоянно: 🚬 Привычки — 17500₽/мес","category":"💰 Лимит","связь":"привычки","ключ":"постоянно_привычки"}\n'
    '  "постоянный расход бьюти 3500" → {"fact":"постоянно: 💅 Бьюти — 3500₽/мес","category":"💰 Лимит","связь":"бьюти","ключ":"постоянно_бьюти"}\n'
    '  "постоянный расход проезд 3000" → {"fact":"постоянно: 🚕 Проезд — 3000₽/мес","category":"💰 Лимит","связь":"проезд","ключ":"постоянно_проезд"}\n'
    '  "постоянный расход вода 2500" → {"fact":"постоянно: 💧 Вода — 2500₽/мес","category":"💰 Лимит","связь":"вода","ключ":"постоянно_вода"}\n'
    '  "цель телефон 100000" → {"fact":"цель: 📱 Телефон — 100000₽ · откладываю 0₽/мес","category":"💰 Лимит","связь":"телефон","ключ":"цель_телефон"}\n'
    '  "цель ПК 200000" → {"fact":"цель: 💻 ПК — 200000₽ · откладываю 0₽/мес","category":"💰 Лимит","связь":"пк","ключ":"цель_пк"}\n'
    '  "долг подружке 50000 до апреля" → {"fact":"долг: 👩 Подружка — 50000₽ · дедлайн: апрель 2026","category":"💰 Лимит","связь":"подружка","ключ":"долг_подружка"}\n'
    '  "долг другу 40000" → {"fact":"долг: 👤 Друг — 40000₽","category":"💰 Лимит","связь":"друг","ключ":"долг_друг"}\n'
    '  "бюджет 100000 рублей" → {"fact":"доход: 💰 Бюджет — 100000₽/мес","category":"💰 Лимит","связь":"бюджет","ключ":"income_бюджет"}\n'
    '  "у меня 100к в месяц" → {"fact":"доход: 💰 Бюджет — 100000₽/мес","category":"💰 Лимит","связь":"бюджет","ключ":"income_бюджет"}\n'
    '  "мой месячный доход 90000" → {"fact":"доход: 💰 Бюджет — 90000₽/мес","category":"💰 Лимит","связь":"бюджет","ключ":"income_бюджет"}\n'
    '  "убери постоянный расход интернет" → {"fact":"постоянно: интернет — 0₽/мес","category":"💰 Лимит","связь":"интернет","ключ":"постоянно_интернет"}\n'
    '  "измени постоянный квартира на 26000" → {"fact":"постоянно: 🏠 Жильё — 26000₽/мес","category":"💰 Лимит","связь":"ж***","ключ":"постоянно_ж***"}\n'
    "ВАЖНО: ключ для постоянных ВСЕГДА начинается с 'постоянно_', для целей — 'цель_', для долгов — 'долг_', для лимитов — 'лимит_', для дохода/бюджета — 'income_'.\n"
    "ВАЖНО: 'подушка' / 'финансовая подушка' — это НЕ цель и НЕ покупка. Обрабатывается отдельным трекером. НИКОГДА не создавай ключ 'цель_подушка'.\n"
    "ВАЖНО: 'постоянный расход' (он же 'обязательный расход') — ежемесячно повторяющийся платёж. Разовые траты этого месяца в память НЕ пишем.\n"
    "ВАЖНО: формулировки вида 'бюджет X рублей', 'у меня Xк в месяц', 'мой доход X' — это ДОХОД: ключ начинается с 'income_', fact в формате 'доход: 💰 Бюджет — X₽/мес'.\n"
    "ВАЖНО: 'убери постоянный расход X' → запиши fact с суммой 0 (будет обновлена существующая запись, а сумма 0 = деактивация).\n"
    "ВАЖНО: 'измени постоянный X на Y' → запиши fact с новой суммой (будет обновлена существующая запись)."
)

_STRIP_RE = re.compile(r"^\s*запомни\s+(что\s+)?", re.IGNORECASE)

# Долг из fact: сумма в ₽ + опциональный "дедлайн: <месяц> <год>"
_DEBT_AMOUNT_RE = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]")
_DEBT_DEADLINE_RE = re.compile(r"дедлайн:\s*(.+)", re.IGNORECASE)


def _parse_debt_from_fact(fact: str) -> Tuple[float, Optional[str]]:
    """'долг: 👩 X — 50000₽ · дедлайн: апрель 2026' → (50000.0, 'апрель 2026')."""
    amount = 0.0
    m = _DEBT_AMOUNT_RE.search(fact)
    if m:
        try:
            amount = float(m.group(1).replace(" ", "").replace(",", "."))
        except ValueError:
            amount = 0.0
    deadline: Optional[str] = None
    dm = _DEBT_DEADLINE_RE.search(fact)
    if dm:
        deadline = dm.group(1).strip().rstrip("·").strip() or None
    return amount, deadline


# «цель: 💻 ПК — 200000₽ · откладываю 15000₽/мес»
_GOAL_FACT_RE = re.compile(
    r"цель:\s*(.+?)\s*[—\-]\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р]"
    r"(?:.*?откладываю\s*(\d[\d\s]*(?:[.,]\d+)?)\s*[₽р])?",
    re.IGNORECASE,
)


def _parse_goal_from_fact(fact: str, fallback_name: str) -> Tuple[str, float, float]:
    """'цель: 💻 ПК — 200000₽ · откладываю 15000₽/мес' → ('💻 ПК', 200000.0, 15000.0)."""
    m = _GOAL_FACT_RE.search(fact or "")
    if not m:
        return (fallback_name.strip() or "цель"), 0.0, 0.0

    def _n(s: str) -> float:
        try:
            return float((s or "0").replace(" ", "").replace(",", "."))
        except ValueError:
            return 0.0

    return m.group(1).strip(), _n(m.group(2)), (_n(m.group(3)) if m.group(3) else 0.0)


# Диверсия долгов/целей в таблицы `debts`/`goals` живёт в parse_and_store
# (общее ядро бота и Mini App). Старый _save_debt_from_memory удалён.


# ── Парсинг факта через Haiku ──────────────────────────────────────────────────

def _single_fact(parsed) -> dict:
    """Haiku иногда отвечает массивом фактов вместо одного объекта (баг:
    "'list' object has no attribute 'get'"). Берём категорию/ключ первого,
    факты склеиваем — ничего из сказанного не теряем."""
    if isinstance(parsed, dict):
        return parsed
    items = [p for p in parsed if isinstance(p, dict)] if isinstance(parsed, list) else []
    if not items:
        return {}
    first = dict(items[0])
    if len(items) > 1:
        facts = [(p.get("fact") or "").strip() for p in items]
        first["fact"] = "; ".join(f for f in facts if f)
    return first


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
        parsed = _single_fact(json.loads(raw))
        fact     = (parsed.get("fact")     or "").strip()
        category = (parsed.get("category") or "").strip()
        связь    = (parsed.get("связь")    or "").strip()
        ключ     = (parsed.get("ключ")     or "").strip()
        if fact and ключ:
            if category not in CATEGORIES:
                category = "💡 Инсайт"
            # Страховка: Haiku иногда оставляет имя только в «связь», а из
            # fact выкидывает («день рождения 17 октября» без «Хена»).
            if связь and category in ("👥 Люди", "🐾 Коты"):
                stem = _normalize_word(связь.lower())
                if stem and stem not in fact.lower():
                    fact = f"{связь[:1].upper()}{связь[1:]}: {fact}"
            return fact, category, связь, ключ
    except Exception as e:
        logger.error("memory _parse_fact error: %s", e)

    fact = _STRIP_RE.sub("", text).strip() or text
    return fact, "💡 Инсайт", "", "факт"


# ── Поиск ──────────────────────────────────────────────────────────────────────

_SEARCH_STOP = {"про", "о", "об", "и", "не", "это", "что", "как", "из", "по",
                "для", "на", "в", "с", "к", "у", "за", "от"}

# _normalize_word — падежный стеммер, вынесен в core/ru_morph (#136,
# переиспользуется матчингом имён долгов). Импорт — в шапке модуля.


def _tokenize_hint(hint: str) -> List[str]:
    """Разбить hint на нормализованные токены, отфильтровав стоп-слова."""
    tokens = []
    for w in hint.lower().split():
        w = w.strip(".,!?;:«»\"'")
        if len(w) >= 2 and w not in _SEARCH_STOP:
            tokens.append(_normalize_word(w))
    return tokens


async def _find_pages_by_hint(
    hint: str, page_size: int = 10, use_semantic: bool = True, user_id: str = "",
    confirmed_only: bool = True,
) -> List[Memory]:
    """Умный поиск по hint через PG. Возвращает List[Memory].

    `user_id` — owner-ключ (#202): пусто → поиск по всей памяти владельца
    (текущее поведение для одного владельца на нескольких устройствах —
    все identity-строки делят один user_id). Непусто → фильтр по владельцу.

    `confirmed_only` (ADR-0027): чтение для контекста/поиска видит только
    review_status='confirmed'. Деструктивные пути (забудь/удали) передают
    False — pending-факт тоже должен быть удаляем.

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
            results = await _mem_repo.find_by_category(
                matched_cat, is_current=True, user_id=user_id, page_size=100,
                confirmed_only=confirmed_only,
            )
            logger.info("_find_pages_by_hint category shortcut: found=%d", len(results))
            return results
        except Exception as e:
            logger.error("_find_pages_by_hint category shortcut error: %s", e)

    tokens = _tokenize_hint(hint)
    logger.info("memory _find_pages_by_hint: hint=%r tokens=%s", hint, tokens)

    # use_semantic=True — информационный поиск: единый гибрид (FTS + vector + RRF),
    # без Haiku-реранка (вызывающему дальше всё равно LLM). Показывает список.
    if use_semantic:
        try:
            found_hits = await _memory_search.search_memories(
                hint, user_id=user_id, k=page_size, do_rerank=False,
            )
            logger.info("_find_pages_by_hint: hybrid found=%d", len(found_hits))
            return found_hits
        except Exception as e:
            logger.error("memory _find_pages_by_hint hybrid: %s", e, exc_info=True)
            return []

    search_terms = tokens if tokens else [hint.strip()]
    # use_semantic=False → деструктивный путь (удаление/деактивация/алиас,
    # см. вызовы ниже по файлу) — точный ILIKE, match_all=True (AND по токенам).
    # Иначе общее слово вроде «доход» ILIKE-OR матчило ЛЮБОЙ факт с «доход» —
    # включая единственный текущий income_-факт, даже без остальных слов
    # хинта в нём (баг: «удали доход робот пылесос» снесло факт «Наследство»,
    # хотя «робот»/«пылесос» в нём нет). Это поведение НЕ менять.
    try:
        results = await _mem_repo.search(
            search_terms, user_id=user_id, page_size=page_size,
            match_all=True, confirmed_only=confirmed_only,
        )
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
    user_id: str = "",
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
        # use_semantic=False: recall алиаса должен быть точным (иначе
        # semantic-довесок может подменить имя на «похожее» на каждом
        # уровне рекурсии) — см. #184.
        mems = await _find_pages_by_hint(связь, use_semantic=False, user_id=user_id)
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
                        primary, user_id,
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


PEOPLE_CATEGORY = "👥 Люди"


def _single_person(user_id: str, mention: str) -> Optional[dict]:
    """Ровно один не-rejected, не-self человек по упоминанию → его карточка; иначе None (без догадок)."""
    try:
        from core.repos import pg_people_repo as prepo
        found = prepo.resolve(user_id, [mention])
        if len(found) != 1:
            return None
        return prepo.get_person(user_id, found[0]["id"])
    except Exception:
        logger.warning("person resolve for memory save failed, saving as plain fact", exc_info=True)
        return None


async def parse_and_store(text: str, user_id: str, bot_label: str) -> dict:
    """Message-free ядро сохранения памяти: раскладка → Haiku-парс →
    диверсия долгов в `debts` → канонизация алиаса → запись.

    Возвращает `{kind, fact, category, key, memory_id, was_updated, debt_name}`.
    `kind` ∈ {"debt", "memory", "error"}. Используется Mini App FAB (#6);
    `save_memory` (бот) вызывает то же и добавляет реплаи/СДВГ-совет/плашку.
    """
    text = maybe_convert(text.strip())
    fact, category, связь, ключ = await _parse_fact(text)
    scope = bot_to_scope(bot_label)

    if ключ.startswith("долг_"):
        name = (связь or ключ[len("долг_"):]).strip()
        amount, deadline = _parse_debt_from_fact(fact)
        try:
            from core.repos.pg_debts_repo import _repo as _debt_repo
            await _debt_repo.upsert(user_id, name, "i_owe", amount=amount, deadline=deadline)
            return {"kind": "debt", "fact": fact, "category": category, "key": ключ,
                    "memory_id": None, "was_updated": False, "debt_name": name, "link": name}
        except Exception as e:
            logger.error("parse_and_store: debt upsert error %s", e)
            return {"kind": "error", "fact": fact, "category": category, "key": ключ,
                    "memory_id": None, "was_updated": False, "debt_name": name, "link": name}

    # Цель → таблица goals (#205), не строка Памяти (аналог долг_ выше).
    if ключ.startswith("цель_") and ключ != "цель_подушка":
        g_name, g_target, g_monthly = _parse_goal_from_fact(fact, связь or ключ[len("цель_"):])
        try:
            from core.repos.pg_goals_repo import _repo as _goals_repo
            await _goals_repo.upsert(user_id, g_name, target=g_target, monthly=g_monthly)
            return {"kind": "goal", "fact": fact, "category": category, "key": ключ,
                    "memory_id": None, "was_updated": False, "debt_name": "", "link": g_name}
        except Exception as e:
            logger.error("parse_and_store: goal upsert error %s", e)
            return {"kind": "error", "fact": fact, "category": category, "key": ключ,
                    "memory_id": None, "was_updated": False, "debt_name": "", "link": g_name}

    if category != "💰 Лимит" and связь:
        original_link = связь
        canonical_link = await _resolve_alias(связь, user_id)
        if canonical_link and canonical_link != original_link:
            связь = canonical_link
            old_key = ключ
            orig_lower = original_link.lower()
            if old_key.lower().startswith(orig_lower + "_"):
                ключ = canonical_link.lower() + old_key[len(original_link):]
            elif old_key.lower() == orig_lower:
                ключ = canonical_link.lower()
            logger.info("memory: canonicalized link %r→%r, key %r→%r",
                        original_link, canonical_link, old_key, ключ)

    # PR-3b: «запомни, что Оля любит X» → ровно один человек из «Людей» ⇒ факт привязан к нему
    # (person_id, confirmed, source=manual, ключ карточки). Ноль/несколько совпадений — как раньше.
    person = None
    if category == PEOPLE_CATEGORY and связь:
        person = await asyncio.to_thread(_single_person, user_id, связь)

    try:
        was_updated = False
        if person:
            from core.people import fact_hash, guess_fact_type, related_to_str
            from core.repos import pg_people_repo as prepo
            ключ = "person_%d_%s_%s" % (person["id"], guess_fact_type(fact), fact_hash(fact))
            mid, was_updated = await _mem_repo.upsert(
                fact, ключ, category, scope, related_to_str(person["name"], person.get("aliases") or []),
                "manual", user_id, person_id=person["id"], review_status="confirmed",
            )
            if mid:                                  # вернуть отклонённый раньше факт (как ручной ввод в карточке)
                await asyncio.to_thread(prepo.set_fact_status, user_id, person["id"], int(mid), "confirmed")
        elif category == "💰 Лимит" and ключ:
            mid, was_updated = await _mem_repo.upsert(
                fact, ключ, category, scope, связь, "manual", user_id
            )
        else:
            mid = await _mem_repo.add(
                fact, ключ, category, scope, связь, "manual", user_id
            )
    except Exception as e:
        logger.error("parse_and_store: write error %s", e)
        return {"kind": "error", "fact": fact, "category": category, "key": ключ,
                "memory_id": None, "was_updated": False, "debt_name": "", "link": связь}
    return {"kind": "memory" if mid else "error", "fact": fact, "category": category,
            "key": ключ, "memory_id": mid, "was_updated": was_updated, "debt_name": "", "link": связь,
            "person_id": person["id"] if person else None}


async def save_memory(
    message: Message,
    text: str,
    user_id: str,
    bot_label: str,
) -> None:
    """Распарсить текст через Haiku и сохранить факт в PG (бот-путь)."""
    logger.info("memory save: text=%r bot=%s", text[:60], bot_label)
    r = await parse_and_store(text, user_id, bot_label)
    fact, category, ключ = r["fact"], r["category"], r["key"]
    связь = r.get("link", "")
    scope = bot_to_scope(bot_label)

    if r["kind"] == "debt":
        await message.answer(f"📋 Добавил долг: {fact}")
        return
    if r["kind"] == "goal":
        await message.answer(f"🎯 Добавил цель: {fact}")
        return
    if r["kind"] == "error":
        await message.answer("⚠️ Ошибка записи в базу")
        return

    result = r["memory_id"]
    was_updated = r["was_updated"]
    try:
        if result:
            logger.info("memory save: %s id=%s", "updated" if was_updated else "created", result)
            if was_updated:
                if ключ.startswith("постоянно_"):
                    if "0₽" in fact or "— 0" in fact:
                        await message.answer(f"🧠 Убрал постоянный расход: {связь or ключ}")
                    else:
                        await message.answer(f"🧠 Обновил постоянный расход: {fact}")
                else:
                    await message.answer(f"🧠 Обновил лимит: {fact}")
            else:
                if ключ.startswith("постоянно_"):
                    await message.answer(f"📌 Добавил постоянный расход: {fact}")
                else:
                    cat_label = f" [{category}]" if category else ""
                    sent = await message.answer(f"🧠 Запомнил{cat_label}: {fact}")
                    # Регистрируем плашку для reply-исправлений (#188) — БЕЗ
                    # этого reply на «🧠 Запомнил» падал в общий classify() с
                    # текстом-контекстом, который Haiku не понимал ({"type":
                    # "unknown"}). Только для обычных фактов: бюджетные ветки
                    # (постоянно_) уже правятся через /budget; цели/долги —
                    # свои таблицы (goals/debts), в Память не пишутся.
                    try:
                        from core.message_pages import save_message_page
                        await save_message_page(
                            chat_id=sent.chat.id, message_id=sent.message_id,
                            page_id=result, page_type="memory", bot=scope,
                        )
                    except Exception as e:
                        logger.warning("memory save: register message_page failed: %s", e)
                if category == "🦋 СДВГ":
                    try:
                        tip = await _get_adhd_tip(fact)
                        if tip:
                            await message.answer(tip)
                    except Exception as e:
                        logger.debug("adhd tip error: %s", e)
            # RAG-индексация теперь спущена в PgMemoryRepo.add/upsert (#186) —
            # единая точка для ВСЕХ писателей (save_memory, auto-suggest,
            # бюджет, локация), не только этого пути.
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


_STRUCTURED_ROUTES = {"direct", "birthday", "unknown_person"}
_MEMALL_TTL = 7 * 24 * 3600      # кнопка «Все факты» живёт неделю и переживает рестарт (SQLite pending_kv)
_TYPE_MARK = {"important": "⚠️", "dislikes": "🚫", "likes": "👍"}


def _what(types) -> str:
    t = set(types or ())
    if t == {"likes"}:
        return "что любит"
    if t == {"important"}:
        return "важное"
    return "что не любит / важное"


def _structured_text(result, arcana: bool = False) -> str:
    """Текст ответа для маршрутов с понятой структурой (HTML). Пусто → честное «не знаю»."""
    from html import escape
    from core.repos.pg_people_repo import fact_type
    if result.route == "unknown_person":
        verb = "Не нашла" if arcana else "Не нашёл"
        return f"🧠 {verb} в Людях «{escape(result.unknown_name)}»"
    if result.route == "birthday":
        lines = []
        for g in result.groups:
            lines.append(f"🎂 <b>{escape(g.label)}</b>: {g.birthday if g.birthday else 'ДР не указан'}")
        return "\n".join(lines) or "🧠 Не знаю"
    # direct
    blocks = []
    empty = []
    for g in result.groups:
        if not g.hits:
            empty.append(g.label)
            continue
        rows = [f"{_TYPE_MARK.get(_memory_search.effective_type(h.memory.fact, fact_type(h.memory.key)), '•')} {escape(owner_fact(h.memory.fact, h.memory.related_to, h.memory.person_id))}"
                for h in g.hits]
        blocks.append(f"👤 <b>{escape(g.label)}</b>:\n" + "\n".join(rows))
    if empty and not blocks:
        who = " и ".join(f"<b>{escape(x)}</b>" for x in empty)
        return f"🧠 Не знаю: у {who} нет подтверждённых фактов ({_what(result.types)})"
    if empty:
        blocks.append("Нет подтверждённых фактов (" + _what(result.types) + "): "
                      + ", ".join(escape(x) for x in empty))
    return _fit_telegram("\n\n".join(blocks))


_TG_LIMIT = 3900          # у Telegram лимит 4096 символов на сообщение; запас под разметку и клавиатуру


def _fit_telegram(text: str) -> str:
    """Режет по границе строки и пишет, сколько строк не поместилось (HTML-теги не рвём: режем только целые строки)."""
    if len(text) <= _TG_LIMIT:
        return text
    lines = text.split("\n")
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > _TG_LIMIT - 40:
            out.append(f"… и ещё {len(lines) - i} стр.")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _format_memory_list(mems: List[Memory]) -> str:
    """Текст списка найденных записей памяти (HTML): СДВГ по группам, остальные с категорией и датой."""
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
            lines.append(f"{cat_emoji} {owner_fact(m.fact, m.related_to, m.person_id)}{inactive_mark}\n{line2}")
    except Exception as e:
        logger.error("search_memory formatting error: %s", e, exc_info=True)
        lines = []
        for m in mems:
            cat_emoji = m.category.split(" ")[0] if m.category else "💡"
            inactive_mark = " <i>(неактуально)</i>" if not m.is_current else ""
            line2 = f"<i>{m.category} · {m.date}</i>" if m.category else f"<i>{m.date}</i>"
            lines.append(f"{cat_emoji} {owner_fact(m.fact, m.related_to, m.person_id)}{inactive_mark}\n{line2}")

    all_cats = set(m.category for m in mems)
    if len(all_cats) == 1:
        single_cat = all_cats.pop()
        header = f"{single_cat} ({len(mems)} зап.)"
    else:
        header = f"🧠 <b>Память</b> (найдено {len(mems)})"
    return f"{header}:\n\n" + "\n\n".join(lines)


async def search_memory(
    message: Message,
    query: str,
    user_id: str,
    del_prefix: str = "mem_del",
) -> None:
    """Поиск по памяти + финансам. Вопрос сперва разбирается (core.query_parse): ясный субъект+тип →
    прямой запрос по человеку, «не знаю» вместо похожих фактов; иначе — единый гибрид."""
    query = query.strip()
    result = None

    if query:
        fin_task = asyncio.ensure_future(_search_finance(query, page_size=5))
        result = await _memory_search.answer(query, user_id=user_id, k=5, do_rerank=True)
        if result.route in _STRUCTURED_ROUTES:
            fin_task.cancel()
            fin_pages: list = []
        else:
            fin_pages = await fin_task
        mems = result.memories
        logger.info("memory search: hint=%r route=%s mems=%d fin=%d",
                    query, result.route, len(mems), len(fin_pages))
        if result.route in _STRUCTURED_ROUTES and not mems:
            await message.answer(_structured_text(result, arcana=del_prefix.startswith("arc")))
            return
    else:
        try:
            mems = await _mem_repo.find_recent(is_current=True, user_id=user_id, page_size=10)
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

    # ── Ответ своими словами (этап 4): только факты из Result; любой сбой/нарушение grounding → список ──
    if result is not None and mems and _memory_answer.eligible(result):
        arcana = del_prefix.startswith("arc")
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        outcome = await _memory_answer.generate(result, query, arcana=arcana)
        if outcome.ok:
            token = secrets.token_hex(4)
            _pkv.save(uid, f"memall_{token}", {
                "route": result.route, "types": list(result.types), "subject": result.subject,
                "groups": [{"label": g.label, "person_id": g.person_id, "ids": [int(h.memory.id) for h in g.hits]}
                           for g in result.groups if g.hits],
            }, ttl=_MEMALL_TTL)
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=f"Все факты ({len(mems)})", callback_data=f"memall:{token}")]])
            await message.answer(outcome.text, reply_markup=kb)
            return

    if result is not None and result.route == "direct" and mems:
        text = _structured_text(result, arcana=del_prefix.startswith("arc"))
        await message.answer(text, reply_markup=_build_delete_keyboard(
            uid, list(mems), reactivate_cb="mem_reactivate_selected"))
        return

    # ── Память ──
    if mems:
        parts.append(_format_memory_list(mems))

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
    user_id: str,
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
        # use_semantic=False: деактивация применяется КО ВСЕМ найденным без
        # подтверждения — semantic-довесок (top-K ближайших БЕЗ порога
        # похожести) может подмешать нерелевантные факты (#184).
        mems = await _find_pages_by_hint(
            hint, use_semantic=False, user_id=user_id, confirmed_only=False,
        ) if hint else []
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
    user_id: str,
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
        # use_semantic=False: ровно один матч архивируется НЕМЕДЛЕННО без
        # подтверждения — semantic-сосед (без порога похожести) на пустом
        # ILIKE снёс бы не тот факт (#184).
        mems = await _find_pages_by_hint(
            hint, use_semantic=False, user_id=user_id, confirmed_only=False,
        ) if hint else []
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


async def find_memories_by_subject_name(
    name: str, user_id: str = ""
) -> List[Memory]:
    """Публичная обёртка над _find_pages_by_hint для сопоставления субъекта
    расклада с существующей памятью (#189, arcana session subject_id).

    use_semantic=False: сопоставление субъекта («тот же Вадим?») должно быть
    точным — semantic-довесок мог бы подсунуть «похожего» человека вместо
    того же самого (см. ту же оговорку в _resolve_alias, #184).
    Приоритет отдаём записям, где Memory.related_to совпадает с именем
    дословно (регистронезависимо) — это самый сильный сигнал «про того же
    человека», а не просто содержит имя где-то в тексте факта.
    """
    if not name or not name.strip():
        return []
    try:
        mems = await _find_pages_by_hint(name, use_semantic=False, user_id=user_id)
    except Exception as e:
        logger.warning("find_memories_by_subject_name: lookup failed for %r: %s", name, e)
        return []
    if user_id:
        mems = [m for m in mems if not m.user_id or m.user_id == user_id]
    mems = [m for m in mems if m.is_current and not m.is_archived]
    name_lower = name.strip().lower()
    exact = [m for m in mems if (m.related_to or "").strip().lower() == name_lower]
    return exact or mems


def extract_context_keywords(data: dict, client_name: Optional[str] = None) -> List[str]:
    """Извлекает ключевые слова для поиска в памяти из распарсенных данных."""
    keywords: List[str] = []
    if client_name:
        keywords.append(client_name)
    for key in ("spread_type", "deck", "category", "goal", "place", "name",
                "session_name", "subject_name", "question"):
        val = data.get(key)
        if val and isinstance(val, str) and len(val) > 2:
            keywords.append(val)
    # #85: слова из вопросов триплетов — чтобы кодовое слово-фигурант
    # («состояние корабля») попало в поиск по Памяти и Sonnet-трактовка
    # знала, кто такой «корабль».
    for tr in (data.get("triplets") or []):
        q = tr.get("question") if isinstance(tr, dict) else None
        if q and isinstance(q, str):
            keywords.extend(w for w in q.split() if len(w) > 3)
    return keywords


def _people_context_block(user_id: str, keywords: List[str], seen_ids: Set[str]) -> str:
    """Блок «Из Людей» для найденных по упоминанию людей. Один человек — его факты и ДР; несколько
    (тёзки / общая основа) — наборы всех, каждый с пометкой, кто это. Заполняет seen_ids id'ами фактов."""
    from core.people import birthday_context, fact_text
    from core.repos import pg_people_repo as prepo
    people_ = prepo.resolve(user_id, [k for k in keywords if k])
    if not people_:
        return ""
    multi = len(people_) > 1
    out = ["Из «Людей» (подтверждённые факты о упомянутых людях)" +
           (" — совпало несколько человек, неясно, о ком речь:" if multi else ":")]
    for p in people_:
        facts = prepo.confirmed_facts(p["id"])
        bd = birthday_context(p["birthday"], p["birthday_year_known"])
        if not facts and not bd:
            continue
        if multi:
            out.append(f"[{p['name']}]")
        if bd:
            out.append(f"- {bd}")
        for f in facts:
            seen_ids.add(str(f["id"]))
            out.append(f"- {fact_text(p['name'], f['text'])[:200]}")
    return "\n".join(out) if len(out) > 1 else ""


async def get_memories_for_context(
    user_id: str,
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
        # PR-3: упоминание человека («Маше», «с Машей») → его подтверждённые факты + ДР из карточки.
        # Идут мимо фильтра scope (факты о людях общие для обоих ботов) и вне лимита max_results;
        # id попадают в seen_ids, чтобы обычный поиск не дублировал те же записи.
        people_block = ""
        try:
            people_block = await asyncio.to_thread(_people_context_block, user_id, keywords, seen_ids)
        except Exception:
            logger.warning("people mention resolve failed, continuing without it", exc_info=True)
        # Один гибридный запрос по всем ключевым словам (а не N по одному): без
        # Haiku-реранка (дальше всё равно LLM-промпт, она сама отфильтрует), RRF top-N.
        # scope — в самом SQL, поэтому top-N заполняется только подходящими фактами.
        query = " ".join(kw.strip() for kw in keywords if kw and len(kw.strip()) >= 2)
        if query:
            found = await _memory_search.search_memories(
                query, user_id=user_id, scope=scope if scope != "global" else "",
                k=max_results, do_rerank=False,
            )
            for m in found:
                if m.id in seen_ids:
                    continue
                seen_ids.add(m.id)
                mems.append(m)
                if len(mems) >= max_results:
                    break

        if not mems and not people_block:
            return ""

        lines = ["Контекст из памяти:"]
        for m in mems:
            cat_str = f" [{m.category}]" if m.category else ""
            lines.append(f"- {owner_fact(m.fact, m.related_to, m.person_id)[:200]}{cat_str}")
        if people_block:
            lines.append(people_block)

        return "\n".join(lines)
    except Exception:
        logger.warning("get_memories_for_context failed, continuing without context")
        return ""


async def get_figurant_facts(user_id: str, max_results: int = 40) -> List[str]:
    """#85: сырые факты из категории 🎭 Фигуранты (словарь кодовых слов).
    Пустой список → у юзера нет фигурантов (fallback на прежнее поведение)."""
    if not user_id:
        return []
    try:
        mems = await _mem_repo.find_by_category(
            FIGURANT_CATEGORY, is_current=True, user_id=user_id, page_size=max_results,
        )
    except Exception:
        logger.warning("get_figurant_facts failed, continuing without dictionary")
        return []
    return [m.fact.strip() for m in (mems or []) if m.fact and m.fact.strip()]


def figurant_prompt_block(facts: List[str]) -> str:
    """Блок для системного промпта Haiku-парсера сессий. "" если фактов нет."""
    if not facts:
        return ""
    lines = [
        "\n\n═══ СЛОВАРЬ КОДОВЫХ СЛОВ (Память пользователя) ═══",
        "Пользователь прячет имена фигурантов раскладов за нарицательными "
        "кличками. Если в тексте расклада такое слово стоит как СУБЪЕКТ "
        "вопроса («состояние корабля», «что чувствует дом») — это ФИГУРАНТ, "
        "а не метафора.",
        "Для session_name форматов A/B: если есть и клиент, и кодовое слово-"
        "субъект → session_name = '{Клиент} — {кодовое слово} ({реальное имя "
        "если оно указано в словаре})'. Практика (приворот/отворот/...) при "
        "этом идёт в session_category, НЕ в session_name.",
        "Словарь:",
    ]
    lines.extend(f"- {f[:200]}" for f in facts)
    return "\n".join(lines)


async def get_figurant_dictionary(user_id: str, max_results: int = 40) -> str:
    """Готовый блок для системного промпта парсера. "" если фигурантов нет."""
    return figurant_prompt_block(await get_figurant_facts(user_id, max_results))


async def auto_suggest_memory(
    message: Message,
    text: str,
    user_id: str,
    bot_label: str,
    pending_store: Dict[int, dict],
    yes_prefix: str = "mem_auto_yes",
    no_prefix: str  = "mem_auto_no",
) -> None:
    """Предложить сохранить факт в память (inline да/нет)."""
    if not text or not text.strip() or text.strip() in ("()", "(  )"):
        return
    uid = message.from_user.id
    pending_store[uid] = {"text": text, "user_id": user_id}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧠 Да, запомнить", callback_data=f"{yes_prefix}:{uid}"),
        InlineKeyboardButton(text="✗ Нет",            callback_data=f"{no_prefix}:{uid}"),
    ]])
    await message.answer(
        f"💡 Сохранить в память?\n<i>{text[:100]}</i>",
        reply_markup=kb,
    )


async def expand_all_facts(call) -> None:
    """Кнопка «Все факты (N)» под сгенерированным ответом: показывает прежний список с чекбоксами
    («неактуально» / удаление / восстановление работают как в обычном поиске)."""
    await call.answer()
    uid = call.from_user.id
    token = (call.data or "").split(":", 1)[-1]
    st = _pkv.get(uid, f"memall_{token}", ttl=_MEMALL_TTL)
    if not st:
        await call.message.answer("⏱ Этот список уже устарел — спроси ещё раз.")
        return
    ids = [i for g in st.get("groups", []) for i in g.get("ids", [])]
    loaded = await asyncio.to_thread(_memory_search._load, ids)
    mems = [loaded[i] for i in ids if i in loaded]
    if not mems:
        await call.message.answer("🧠 Этих записей уже нет.")
        return
    _last_memory_results[uid] = list(mems)
    _mem_delete_pages[uid] = list(mems)
    _mem_selected[uid] = set()
    if st.get("route") == "direct":
        by_id = {m.id: m for m in mems}
        groups = [_memory_search.Group(label=g["label"], person_id=g.get("person_id"), hits=[
            _memory_search.Hit(memory_id=i, score=0.0, memory=by_id[str(i)]) for i in g["ids"] if str(i) in by_id])
            for g in st["groups"]]
        res = _memory_search.Result(route="direct", kind="facts", subject=st.get("subject", "person"),
                                    types=tuple(st.get("types") or ()), groups=groups)
        text = _structured_text(res)
    else:
        text = _format_memory_list(mems)
    await call.message.answer(text, reply_markup=_build_delete_keyboard(
        uid, list(mems), reactivate_cb="mem_reactivate_selected"))
