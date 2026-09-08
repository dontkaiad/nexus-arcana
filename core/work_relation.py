"""core/work_relation.py — авто-привязка 🃏 Расклады / 🕯 Ритуалы → 🔮 Работы (PG, #151).

При создании записи (расклад/ритуал) с client_id: найти первую открытую Работу
того же клиента и категории → проставить work_id на записи → закрыть Работу как
Done. Кардинальность 1:1 (FK work_id на sessions/rituals, ADR #151).

#154 стадия 5: если открытой Работы НЕТ — `link_practice_record` заводит её
задним числом и сразу закрывает Done, чтобы у каждого выполненного события
была Работа в истории (для карточки клиента и RAG).
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple, Union

logger = logging.getLogger("core.work_relation")


async def find_active_work_for_client(
    client_id: str, category: str, user_id: str,
) -> Optional[str]:
    """Первая открытая Работа клиента нужной категории (PG). Возвращает work id
    или None. category: '✨ Ритуал' | '🃏 Расклад' — точное совпадение
    works.category. fail-closed: без client_id/user_id → None."""
    if not client_id or not user_id:
        return None
    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        w = await PgWorksRepo().find_active_for_client(client_id, category, user_id)
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


async def link_practice_record(
    entity_type: str,
    record_ids: Union[str, List[str]],
    client_id: Optional[str],
    category: str,
    title: str,
    user_id: str,
) -> Tuple[Optional[str], bool]:
    """#154 стадия 5: привязать выполненную запись(и) к Работе.

    Ищет открытую Работу клиента нужной категории; если нет — создаёт новую.
    Проставляет work_id на все record_ids и закрывает Работу как Done.
    entity_type: 'session' | 'ritual'. category: '🃏 Расклад' | '✨ Ритуал'.
    Возвращает (work_id, created). Без client_id/user_id → (None, False),
    любая ошибка не роняет сохранение записи.
    """
    ids = [record_ids] if isinstance(record_ids, str) else list(record_ids)
    ids = [i for i in ids if i]
    if not client_id or not user_id or not ids:
        return (None, False)
    try:
        from arcana.repos.pg_works_repo import PgWorksRepo
        repo = PgWorksRepo()
        w = await repo.find_active_for_client(client_id, category, user_id)
        work_id = w.id if w else None
        created = False
        if not work_id:
            work_id = await repo.create(
                title=(title or category)[:200],
                category=category,
                client_id=client_id,
                user_id=user_id,
            )
            created = bool(work_id)
        if not work_id:
            return (None, False)
        for rid in ids:
            await set_event_work_id(entity_type, rid, str(work_id))
        await close_work_as_done(str(work_id))
        return (str(work_id), created)
    except Exception as e:
        logger.warning("link_practice_record(%s) failed: %s", entity_type, e)
        return (None, False)
