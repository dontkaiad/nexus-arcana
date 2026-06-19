"""core/work_relation.py — авто-привязка 🃏 Расклады / 🕯 Ритуалы → 🔮 Работы (PG, #151).

При создании записи (расклад/ритуал) с client_id: найти первую открытую Работу
того же клиента и категории → проставить work_id на записи → закрыть Работу как
Done. Кардинальность 1:1 (FK work_id на sessions/rituals, ADR #151). Нет открытой
Работы → work_id остаётся NULL (запись без плановой Работы), не падаем.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("core.work_relation")


async def find_active_work_for_client(
    client_id: str, category: str, user_notion_id: str,
) -> Optional[str]:
    """Первая открытая Работа клиента нужной категории (PG). Возвращает work id
    или None. category: '✨ Ритуал' | '🃏 Расклад' — точное совпадение
    works.category. fail-closed: без client_id/user_notion_id → None."""
    if not client_id or not user_notion_id:
        return None
    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        w = await PgWorksRepo().find_active_for_client(client_id, category, user_notion_id)
        return w.id if w else None
    except Exception as e:
        logger.warning("find_active_work_for_client failed: %s", e)
        return None


async def set_event_work_id(entity_type: str, record_id: str, work_id: str) -> bool:
    """Проставить work_id на записи (entity_type: 'session' | 'ritual'). True при успехе."""
    try:
        if entity_type == "session":
            from arcana.repos.pg_sessions_repo import PgSessionsRepo
            return await PgSessionsRepo().set_work_id(record_id, work_id)
        if entity_type == "ritual":
            from arcana.repos.pg_rituals_repo import PgRitualsRepo
            return await PgRitualsRepo().set_work_id(record_id, work_id)
    except Exception as e:
        logger.warning("set_event_work_id(%s) failed: %s", entity_type, e)
    return False


async def close_work_as_done(work_id: str) -> bool:
    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        return await PgWorksRepo().set_status(work_id, "done")
    except Exception as e:
        logger.warning("close_work_as_done failed: %s", e)
        return False
