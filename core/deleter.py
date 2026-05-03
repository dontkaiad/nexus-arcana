"""core/deleter.py — универсальное удаление записей из Notion с подтверждением"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional

from notion_client import AsyncClient
from notion_client.errors import APIResponseError

from core.notion_client import get_notion, db_query, _extract_text, _extract_number
from core.claude_client import ask_claude

logger = logging.getLogger(__name__)
MOSCOW_TZ = timezone(timedelta(hours=3))

PARSE_DELETE_SYSTEM = """Определи параметры удаления из сообщения. Ответь ТОЛЬКО JSON без markdown:
{
  "scope": "today|last|date|month|all",
  "date": "YYYY-MM-DD или null",
  "month": "YYYY-MM или null",
  "count": число (для last N) или 1
}
Примеры:
"удали последнее" → {"scope": "last", "date": null, "month": null, "count": 1}
"удали последние 3" → {"scope": "last", "date": null, "month": null, "count": 3}
"удали все за сегодня" → {"scope": "today", "date": null, "month": null, "count": 1}
"удали все за март" → {"scope": "month", "date": null, "month": "2026-03", "count": 1}
"удали запись от 5 марта" → {"scope": "date", "date": "2026-03-05", "month": null, "count": 1}"""


async def parse_delete_intent(text: str) -> dict:
    import json
    raw = await ask_claude(text, system=PARSE_DELETE_SYSTEM, max_tokens=150,
                           model="claude-haiku-4-5-20251001")
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {"scope": "last", "date": None, "month": None, "count": 1}


async def find_pages_to_delete(
    db_id: str,
    date_field: str,
    scope: str,
    date: Optional[str] = None,
    month: Optional[str] = None,
    count: int = 1,
) -> List[dict]:
    """Находит страницы для удаления по параметрам."""
    today = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")

    if scope == "today":
        filter_obj = {"property": date_field, "date": {"equals": today}}
        return await db_query(db_id, filter_obj=filter_obj,
                              sorts=[{"property": date_field, "direction": "descending"}],
                              page_size=50)

    elif scope == "last":
        pages = await db_query(db_id,
                               sorts=[{"property": date_field, "direction": "descending"}],
                               page_size=count)
        return pages[:count]

    elif scope == "date" and date:
        filter_obj = {"property": date_field, "date": {"equals": date}}
        return await db_query(db_id, filter_obj=filter_obj, page_size=50)

    elif scope == "month" and month:
        filter_obj = {"property": date_field, "date": {"on_or_after": f"{month}-01"}}
        pages = await db_query(db_id, filter_obj=filter_obj, page_size=100)
        return [p for p in pages if
                (p["properties"].get(date_field, {}).get("date", {}) or {}).get("start", "")[:7] == month]

    elif scope == "all":
        return await db_query(db_id, page_size=100)

    return []


async def delete_pages(page_ids: List[str]) -> int:
    """Архивирует (удаляет) страницы. Возвращает кол-во удалённых."""
    notion = get_notion()
    deleted = 0
    for pid in page_ids:
        try:
            await notion.pages.update(page_id=pid, archived=True)
            deleted += 1
        except APIResponseError as e:
            logger.error("delete page %s: %s", pid, e)
    return deleted


def format_page_preview(page: dict, title_field: str = "", date_field: str = "") -> str:
    """Формирует строку предпросмотра записи."""
    props = page.get("properties", {})

    # Дата
    date_val = ""
    if date_field and date_field in props:
        d = (props[date_field].get("date") or {}).get("start", "")
        date_val = d[:10] if d else ""

    # Название/описание
    title = ""
    for field in [title_field, "Название", "Текст", "Вопрос", "Имя"]:
        if field and field in props:
            t = _extract_text(props[field])
            if t:
                title = t[:50]
                break

    # Сумма если есть
    amount = ""
    for field in ["Сумма", "Сумма клиенту"]:
        if field in props:
            n = _extract_number(props[field])
            if n:
                amount = f" — {n:,.0f}₽"
                break

    return f"{date_val} {title}{amount}".strip()
