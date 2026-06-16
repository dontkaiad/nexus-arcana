"""miniapp/backend/routes/memory.py — GET /api/memory, GET /api/memory/adhd (PG-native)."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query

from core.config import config
from core.claude_client import ask_claude
from core.user_manager import get_user_notion_id
from core.repos.pg_memory_repo import PgMemoryRepo, Memory

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
    "🔒 Обязательные",
    "💰 Лимит",
    "📋 Долги",
    "🎯 Цели",
}

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
    for mem in raw:
        c = mem.category or None
        if c in EXCLUDED_CATEGORIES:
            continue
        if c:
            categories.add(c)
        items.append(_serialize_memory(mem))

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

    # #49(b): объединяем канонический список с теми, что реально есть в данных.
    seen = set(CANONICAL_CATEGORIES)
    extra = sorted(c for c in categories if c not in seen)
    all_cats = list(CANONICAL_CATEGORIES) + extra

    return {
        "items": items,
        "categories": all_cats,
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
