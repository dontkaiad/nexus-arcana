"""miniapp/backend/routes/memory.py — GET /api/memory, GET /api/memory/adhd."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from core.config import config
from core.notion_client import query_pages
from core.claude_client import ask_claude
from core.user_manager import get_user_notion_id

from miniapp.backend import cache
from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import rich_text, select_name, title_text

logger = logging.getLogger("miniapp.memory")

router = APIRouter()

# Категории, которые принадлежат бюджету и ADHD — исключаем из общего /api/memory,
# для них есть /api/finance и /api/memory/adhd.
EXCLUDED_CATEGORIES = {
    "🧠 СДВГ",
    "📥 Доход",
    "🔒 Обязательные",
    "💰 Лимит",
    "📋 Долги",
    "🎯 Цели",
}


def _serialize(page: dict) -> dict:
    props = page.get("properties", {})
    return {
        "id": page.get("id", ""),
        "text": title_text(props.get("Текст", {})),
        "cat": select_name(props.get("Категория", {})) or None,
        "related": rich_text(props.get("Связь", {})) or None,
        "key": rich_text(props.get("Ключ", {})) or None,
    }


async def _fetch_actual(user_notion_id: str) -> list[dict]:
    """Все актуальные записи Памяти юзера (Актуально == true)."""
    db_id = config.nexus.db_memory
    if not db_id:
        return []
    conditions: list[dict] = [
        {"property": "Актуально", "checkbox": {"equals": True}},
    ]
    if user_notion_id:
        conditions.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    filt = {"and": conditions} if len(conditions) > 1 else conditions[0]
    return await query_pages(db_id, filters=filt, page_size=500)


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
    for p in raw:
        item = _serialize(p)
        c = item["cat"]
        if c in EXCLUDED_CATEGORIES:
            continue
        if c:
            categories.add(c)
        items.append(item)

    if cat:
        items = [i for i in items if i["cat"] == cat]
    if q:
        needle = q.lower().strip()
        items = [i for i in items if needle in (i["text"] or "").lower()]

    return {
        "items": items,
        "categories": sorted(categories),
    }


# ── /api/memory/adhd ────────────────────────────────────────────────────────

async def _adhd_records(user_notion_id: str) -> list[dict]:
    db_id = config.nexus.db_memory
    if not db_id:
        return []
    conditions: list[dict] = [
        {"property": "Категория", "select": {"equals": "🧠 СДВГ"}},
        {"property": "Актуально", "checkbox": {"equals": True}},
    ]
    if user_notion_id:
        conditions.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    return await query_pages(db_id, filters={"and": conditions}, page_size=100)


async def _generate_adhd_profile(tg_id: int, records: list[dict]) -> str:
    cached = cache.get_profile(tg_id)
    if cached:
        return cached["text"]

    if not records:
        return ""

    lines = []
    for r in records:
        t = title_text(r.get("properties", {}).get("Текст", {}))
        if t:
            lines.append(f"- {t}")
    context = "\n".join(lines)
    prompt = (
        "Вот что я знаю про её СДВГ-паттерны, триггеры и работающие стратегии:\n\n"
        f"{context}"
    )
    system = (
        "Ты — внешний мозг Кай. Сгенерируй персональный СДВГ-профиль Кай "
        "на основе этих записей: паттерны, триггеры, стратегии. "
        "Женский род. 2-3 абзаца живого текста без буллетов."
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
    text = (text or "").strip()
    if text:
        cache.set_profile(tg_id, text)
    return text


@router.get("/memory/adhd")
async def get_memory_adhd(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    raw = await _adhd_records(user_notion_id)
    records = [
        {
            "id": p.get("id", ""),
            "text": title_text(p.get("properties", {}).get("Текст", {})),
        }
        for p in raw
    ]
    profile = await _generate_adhd_profile(tg_id, raw)
    return {
        "profile": profile,
        "records": [r for r in records if r["text"]],
    }
