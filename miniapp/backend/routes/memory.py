"""miniapp/backend/routes/memory.py — GET /api/memory, GET /api/memory/adhd (PG-native)."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query

import re
from datetime import datetime

from core.config import config
from core.claude_client import ask_claude
from core.user_manager import get_user_notion_id
from core.repos.pg_memory_repo import PgMemoryRepo, Memory
from core.budget import (
    INCOME_RE,
    LIMIT_AMOUNT_RE,
    LIMIT_FACT_RE,
    ONE_TIME_FACT_RE,
    PERMANENT_RE,
    parse_amount,
)

from miniapp.backend import cache
from miniapp.backend.auth import current_user_id

logger = logging.getLogger("miniapp.memory")

router = APIRouter()
_memory_repo = PgMemoryRepo()

# Категории, которые принадлежат бюджету и ADHD — исключаем из общего /api/memory,
# для них есть /api/finance и /api/memory/adhd.
EXCLUDED_CATEGORIES = {
    "🦋 СДВГ",
    "📥 Доход",
    "🔒 Постоянные",
    "📋 Долги",
    "🎯 Цели",
}

# «💰 Лимит» — специальная категория бюджета. НЕ показываем плоским списком
# (сырые строки «постоянно: … — 20000₽/мес»), а отдаём сгруппированной
# структурой по префиксу ключа при cat="💰 Лимит". Записи бюджета опознаём по
# префиксу ключа (в БД все они лежат под категорией «💰 Лимит», но матч по
# ключу надёжнее — не зависит от того, каким путём факт создан).
LIMIT_CATEGORY = "💰 Лимит"
_BUDGET_KEY_PREFIXES = ("income_", "постоянно_", "разовый_", "лимит_")
# лимит_фикс / лимит_разовые — агрегаты (= суммы Постоянных / Разовых, уже
# показанных отдельными группами), не самостоятельные позиции.
_BUDGET_AGGREGATE_KEYS = {"лимит_фикс", "лимит_разовые"}

_RU_MONTHS = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь",
    7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")


def _name_amount(fact: str) -> tuple[str, str, float]:
    """(читаемое_имя, эмодзи, сумма) из fact_text бюджетной записи.

    Переиспользует те же регексы core.budget, что парсер сопоставления
    Фикс/Разовые (PERMANENT_RE / ONE_TIME_FACT_RE), плюс INCOME_RE / LIMIT_FACT_RE.
    """
    for rx in (PERMANENT_RE, ONE_TIME_FACT_RE, INCOME_RE):
        m = rx.search(fact)
        if m:
            raw = m.group(1).strip()
            emoji_m = _EMOJI_RE.search(raw)
            name = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()  # убрать хвост «(🏠 Жильё)»
            return name, (emoji_m.group(0) if emoji_m else ""), parse_amount(m.group(2))

    m = LIMIT_FACT_RE.search(fact)
    if m:
        raw = m.group(1).strip()            # напр. «🍜 Продукты»
        emoji_m = _EMOJI_RE.search(raw)
        emoji = emoji_m.group(0) if emoji_m else ""
        name = raw[len(emoji):].strip() if emoji else raw
        return name, emoji, parse_amount(m.group(2))

    # запас: сумма регексом, имя — вся строка после «:» без суммы
    am = LIMIT_AMOUNT_RE.search(fact)
    amount = float(am.group(1).replace(" ", "").replace(",", ".")) if am else 0.0
    name = re.sub(r"\s*[—-]\s*\d.*$", "", fact.split(":", 1)[-1]).strip() or fact
    return name, "", amount


def _group_budget_memories(mems: list[Memory]) -> list[dict]:
    """[{title, meta, subtitle?, items:[{id,name,emoji,amount,unit}]}] — непустые группы."""
    buckets: dict[str, list[dict]] = {
        "📥 Доход": [], "🔒 Постоянные": [], "📦 Разовые": [], "📊 Лимиты категорий": [],
    }
    for m in mems:
        key = (m.key or "").lower()
        if key in _BUDGET_AGGREGATE_KEYS:
            continue
        if key.startswith("income_"):
            grp = "📥 Доход"
        elif key.startswith("постоянно_"):
            grp = "🔒 Постоянные"
        elif key.startswith("разовый_"):
            grp = "📦 Разовые"
        elif key.startswith("лимит_"):
            grp = "📊 Лимиты категорий"
        else:
            continue
        name, emoji, amount = _name_amount(m.fact or "")
        buckets[grp].append({
            "id": m.id,
            "name": name,
            "emoji": emoji,
            "amount": int(round(amount)),
            "unit": "₽" if grp == "📦 Разовые" else "₽/мес",
        })

    now = datetime.now()
    groups: list[dict] = []
    for title, items in buckets.items():
        if not items:
            continue
        g = {
            "title": title,
            "meta": "{:,} ₽".format(sum(i["amount"] for i in items)).replace(",", " "),
            "items": items,
        }
        if title == "📦 Разовые":
            g["subtitle"] = "{} {}".format(_RU_MONTHS.get(now.month, ""), now.year)
        groups.append(g)
    return groups

# tz_{tg_id}/city_{tg_id} (core/location.py:set_user_location) — не «твоя
# память», а внутреннее состояние бота (нужно погоде и дедлайнам задач).
# Раньше они утекали в общий список как ничего не говорящие "5"/"Гай" —
# Кай приняла их за баг/мусор. Прячем из списка, оставляя рабочими под
# капотом (get_user_tz/_resolve_city_from_memory ходят напрямую по key,
# этот фильтр их не касается).
EXCLUDED_KEY_PREFIXES = ("tz_", "city_")

# #49: канонический список категорий (из core/memory.py CATEGORIES,
# без бюджетных/ADHD). Возвращаем всегда, чтобы фронт показывал все табы,
# даже если в какой-то категории пусто.
CANONICAL_CATEGORIES = [
    "👥 Люди",
    "🏥 Здоровье",
    "🛒 Предпочтения",
    "💼 Работа",
    "🏠 Быт",
    "🔄 Паттерн",
    "💡 Инсайт",
    "🔮 Практика",
    "🐾 Коты",
]


def _all_categories(present: set[str]) -> list[str]:
    """#49(b): канонический список + реально встреченные + всегда «💰 Лимит» (спец-вид)."""
    seen = set(CANONICAL_CATEGORIES)
    extra = sorted(c for c in present if c not in seen and c != LIMIT_CATEGORY)
    return list(CANONICAL_CATEGORIES) + extra + [LIMIT_CATEGORY]


def _serialize_memory(mem: Memory) -> dict:
    return {
        "id": mem.id,
        "text": mem.fact,
        "cat": mem.category or None,
        "related": mem.related_to or None,
        "key": mem.key or None,
    }


async def _fetch_actual(user_notion_id: str) -> List[Memory]:
    """Все актуальные записи Памяти юзера (is_current == True)."""
    try:
        return await _memory_repo.find_by_category(
            "",
            is_current=True,
            user_notion_id=user_notion_id,
            page_size=500,
        )
    except Exception as e:
        logger.warning("_fetch_actual PG query failed: %s", e)
        return []


@router.get("/memory")
async def get_memory(
    tg_id: int = Depends(current_user_id),
    cat: Optional[str] = Query(None, description="фильтр по категории"),
    q: Optional[str] = Query(None, description="case-insensitive contains по тексту"),
) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    raw = await _fetch_actual(user_notion_id)

    items: list[dict] = []
    categories: set[str] = set()
    budget_mems: list[Memory] = []
    for mem in raw:
        if (mem.key or "").lower().startswith(_BUDGET_KEY_PREFIXES):
            budget_mems.append(mem)   # → сгруппированный вид «💰 Лимит», не плоский список
            continue
        c = mem.category or None
        if c in EXCLUDED_CATEGORIES:
            continue
        if (mem.key or "").startswith(EXCLUDED_KEY_PREFIXES):
            continue
        if c:
            categories.add(c)
        items.append(_serialize_memory(mem))

    # «💰 Лимит» — сгруппированный спец-вид (Постоянные / Разовые / Лимиты / Доход)
    if cat == LIMIT_CATEGORY:
        return {
            "items": [],
            "categories": _all_categories(categories),
            "grouped": True,
            "groups": _group_budget_memories(budget_mems),
        }

    if cat:
        items = [i for i in items if i["cat"] == cat]
    if q:
        # Выравнивание с ботом: core.memory._find_pages ищет по Текст+Ключ+Связь.
        needle = q.lower().strip()
        items = [
            i for i in items
            if needle in (i["text"] or "").lower()
            or needle in (i["key"] or "").lower()
            or needle in (i["related"] or "").lower()
        ]

    return {
        "items": items,
        "categories": _all_categories(categories),
    }


# ── /api/memory/adhd ────────────────────────────────────────────────────────

async def _adhd_records(user_notion_id: str) -> List[Memory]:
    try:
        return await _memory_repo.find_by_category(
            "🦋 СДВГ",
            is_current=True,
            user_notion_id=user_notion_id,
            page_size=100,
        )
    except Exception as e:
        logger.warning("_adhd_records PG query failed: %s", e)
        return []


def _clean_profile_text(text: str) -> str:
    """Убирает markdown-заголовки и ведущую подпись «СДВГ-профиль …»."""
    import re
    lines = (text or "").strip().splitlines()
    while lines:
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        stripped = re.sub(r"^#+\s*", "", first).strip().strip("*_").strip()
        if stripped.lower().startswith(("сдвг-профиль", "сдвг профиль", "профиль")):
            lines.pop(0)
            continue
        if first.startswith("#"):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


async def _generate_adhd_profile(tg_id: int, records: List[Memory]) -> str:
    cached = cache.get_profile(tg_id)
    if cached:
        cleaned = _clean_profile_text(cached["text"])
        if cleaned != cached["text"]:
            cache.set_profile(tg_id, cleaned)
        return cleaned

    if not records:
        return ""

    lines = []
    for r in records:
        if r.fact:
            lines.append(f"- {r.fact}")
    context = "\n".join(lines)
    prompt = (
        "Вот что я знаю про её СДВГ-паттерны, триггеры и работающие стратегии:\n\n"
        f"{context}"
    )
    system = (
        "Ты — внешний мозг Кай. Сгенерируй персональный СДВГ-профиль Кай "
        "на основе этих записей: паттерны, триггеры, стратегии. "
        "Женский род. 2-3 абзаца живого текста без буллетов. "
        "Не пиши заголовки, не пиши «СДВГ-профиль» в начале — только сам текст."
    )
    try:
        text = await ask_claude(
            prompt=prompt,
            system=system,
            model=config.model_sonnet,
            max_tokens=800,
            temperature=0,
        )
    except Exception as e:
        logger.error("Sonnet profile generation failed: %s", e)
        return ""
    text = _clean_profile_text(text or "")
    if text:
        cache.set_profile(tg_id, text)
    return text


_PATTERN_KW = (
    "забыва", "теря", "откладыва", "прокрастин", "кладёт",
    "громко", "быстро говор", "утро начинается", "сова",
    "не существует", "неосознанно", "гиперфокус",
)
_STRATEGY_KW = (
    "помогают", "помогает", "стратеги", "витамин", "кольц",
    "будильник", "список", "порядок", "структур", "Monster", "Chapman",
)
_TRIGGER_KW = (
    "мешает", "триггер", "хуже", "шум", "раздраж",
    "плохой сон", "не может найти", "не на виду", "не могу",
)


def _classify_adhd(fact: str) -> str:
    low = fact.lower()
    if any(k in low for k in _PATTERN_KW):
        return "patterns"
    if any(k in low for k in _STRATEGY_KW):
        return "strategies"
    if any(k in low for k in _TRIGGER_KW):
        return "triggers"
    return "specifics"


@router.get("/memory/adhd")
async def get_memory_adhd(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    raw = await _adhd_records(user_notion_id)
    groups: dict[str, list[str]] = {
        "patterns": [], "strategies": [], "triggers": [], "specifics": [],
    }
    for mem in raw:
        if not mem.fact:
            continue
        groups[_classify_adhd(mem.fact)].append(mem.fact)
    profile = await _generate_adhd_profile(tg_id, raw)
    return {
        "profile": profile,
        "groups": groups,
    }
