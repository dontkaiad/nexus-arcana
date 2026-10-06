"""core/user_manager.py — Управление пользователями через core_identity (PG).

get_user() / check_permission() / get_user_id() — публичный API без изменений.
Бэкенд переключён с Notion 🪪 Пользователи на PG core_identity (ADR-0007).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# In-process cache: {tg_id → user_dict} TTL 5 min — same structure as before
_user_cache: Dict[int, dict] = {}
_CACHE_TTL = 300


def _to_user_dict(user) -> dict:
    """Convert IdentityUser → dict format used by middleware + handlers."""
    return {
        "user_id": user.user_id or user.notion_id,  # shared owner key (#202); PK fallback
        "name": user.name,
        "role": user.role,
        "permissions": {
            "nexus": user.perm_nexus,
            "arcana": user.perm_arcana,
            "finance": user.perm_finance,
        },
        "_ts": time.time(),
    }


async def get_user(tg_id: int) -> Optional[dict]:
    """Найти пользователя по TG ID. Возвращает None если не найден."""
    cached = _user_cache.get(tg_id)
    if cached and time.time() - cached.get("_ts", 0) < _CACHE_TTL:
        return cached

    try:
        from core.repos.identity_repo import _repo
        user = await _repo.get_by_tg_id(tg_id)
        if user is None:
            logger.info("get_user(%s): not found in core_identity", tg_id)
            return None
        user_data = _to_user_dict(user)
        _user_cache[tg_id] = user_data
        logger.info("get_user(%s): notion_id=%s role=%s", tg_id, user.notion_id, user.role)
        return user_data
    except Exception as e:
        logger.error("get_user(%s) error: %s", tg_id, e)
        return None


async def check_permission(tg_id: int, feature: str) -> bool:
    """Проверить что у пользователя есть доступ к feature (nexus/arcana/finance)."""
    user = await get_user(tg_id)
    if user is None:
        return False
    return user.get("permissions", {}).get(feature, False)


async def get_user_id(tg_id: int) -> Optional[str]:
    """Вернуть внутренний user id (core_identity PK) — owner-ключ для PG-строк."""
    user = await get_user(tg_id)
    if user is None:
        return None
    return user.get("user_id")


async def get_tg_ids_for_user(user_id: str) -> List[int]:
    """Вернуть все tg_id, резолвящиеся в этот user_id (#202/#306: Кай делит
    один user_id между двумя TG-аккаунтами). Нужно для рассылки напоминаний
    во ВСЕ чаты владельца, а не только в тот, откуда пришло действие —
    иначе напоминание уходит в один конкретный чат (какой — зависит от
    порядка ALLOWED_TELEGRAM_IDS / от того, с какого аккаунта создавалась
    запись), а не туда, где Кай реально смотрит уведомления.

    Итерирует config.allowed_ids и резолвит каждый через get_user (кэш TTL
    5 мин, так что повторные вызовы почти бесплатны). Пустой/неизвестный
    user_id → [].
    """
    if not user_id:
        return []
    from core.config import config
    out: List[int] = []
    for tg_id in config.allowed_ids:
        u = await get_user(tg_id)
        if u and u.get("user_id") == user_id:
            out.append(tg_id)
    return out


def invalidate_cache(tg_id: int = 0) -> None:
    """Сбросить кэш пользователя (или всех если tg_id=0)."""
    if tg_id:
        _user_cache.pop(tg_id, None)
    else:
        _user_cache.clear()
    logger.info("invalidate_cache: tg_id=%s", tg_id or "ALL")
