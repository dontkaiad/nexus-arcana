"""core/work_relation.py — авто-привязка 🃏 Расклады / 🕯 Ритуалы → 🔮 Работы.

В Notion имена relation-полей могут отличаться от описанной схемы (хвосты
вроде «Сеансы» / «Ритуалы»), поэтому имя поля резолвится через
get_database_schema один раз на db-id и кешируется.

Для каждого нового события (расклад/ритуал) находим первую активную
Работу того же клиента нужной категории и привязываем relation +
закрываем Работу как Done.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.config import config
from core.notion_client import (
    _with_user_filter, get_notion, query_pages, update_page,
)

logger = logging.getLogger("core.work_relation")

_RELATION_FIELD_CACHE: dict[str, Optional[str]] = {}


async def find_relation_field_to_works(db_id: str) -> Optional[str]:
    """Возвращает имя relation-поля, ссылающегося на 🔮 Работы, в БД db_id.
    Если поля нет — None. Результат кешируется per-db_id."""
    works_db = (config.arcana.db_works or "").replace("-", "")
    if not works_db:
        return None
    if db_id in _RELATION_FIELD_CACHE:
        return _RELATION_FIELD_CACHE[db_id]
    try:
        schema = await get_notion().databases.retrieve(database_id=db_id)
    except Exception as e:
        logger.warning("schema retrieve failed for %s: %s", db_id[:8], e)
        _RELATION_FIELD_CACHE[db_id] = None
        return None
    for prop_name, prop in (schema.get("properties") or {}).items():
        if prop.get("type") != "relation":
            continue
        target = (prop.get("relation") or {}).get("database_id", "").replace("-", "")
        if target == works_db:
            _RELATION_FIELD_CACHE[db_id] = prop_name
            return prop_name
    _RELATION_FIELD_CACHE[db_id] = None
    return None


async def find_active_work_for_client(
    client_id: str,
    category: str,
    user_notion_id: str,
) -> Optional[str]:
    """Ищет первую открытую Работу клиента нужной категории. Возвращает
    page_id или None.

    category: '✨ Ритуал' | '🃏 Расклад' (значение select-поля «Категория»).
    Status фильтр: != Done И != Complete (как в _works_schedule).
    Сортировка по Дедлайн ASC nulls last.
    """
    db_id = config.arcana.db_works
    if not db_id or not client_id:
        return None
    base = {
        "and": [
            {"property": "👥 Клиенты", "relation": {"contains": client_id}},
            {"property": "Категория", "select": {"equals": category}},
            {"property": "Status", "status": {"does_not_equal": "Done"}},
            {"property": "Status", "status": {"does_not_equal": "Complete"}},
        ]
    }
    filters = _with_user_filter(base, user_notion_id)
    sorts = [{"property": "Дедлайн", "direction": "ascending"}]
    try:
        pages = await query_pages(
            db_id, filters=filters, sorts=sorts, page_size=1,
        )
    except Exception as e:
        logger.warning("active work lookup failed: %s", e)
        return None
    return pages[0]["id"] if pages else None


async def attach_event_to_work(
    *,
    event_db_id: str,
    event_page_id: str,
    work_page_id: str,
) -> bool:
    """Связывает event_page (расклад/ритуал) с work_page через relation-поле,
    которое event-БД имеет на 🔮 Работы. Возвращает True при успехе.
    """
    field_name = await find_relation_field_to_works(event_db_id)
    if not field_name:
        return False
    try:
        await update_page(event_page_id, {
            field_name: {"relation": [{"id": work_page_id}]},
        })
        return True
    except Exception as e:
        logger.warning("attach_event_to_work failed: %s", e)
        return False


async def close_work_as_done(work_page_id: str) -> bool:
    try:
        await update_page(work_page_id, {
            "Status": {"status": {"name": "Done"}},
        })
        return True
    except Exception as e:
        logger.warning("close_work_as_done failed: %s", e)
        return False
