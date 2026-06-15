"""core/user_manager.py — Управление пользователями через core_identity (PG).

get_user() / check_permission() / get_user_notion_id() — публичный API без изменений.
Бэкенд переключён с Notion 🪪 Пользователи на PG core_identity (ADR-0007).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# In-process cache: {tg_id → user_dict} TTL 5 min — same structure as before
_user_cache: Dict[int, dict] = {}
_CACHE_TTL = 300


def _to_user_dict(user) -> dict:
    """Convert IdentityUser → legacy dict format (all callers unchanged)."""
    return {
        "notion_page_id": user.notion_id,
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


async def get_user_notion_id(tg_id: int) -> Optional[str]:
    """Вернуть Notion page ID пользователя для Relation полей."""
    user = await get_user(tg_id)
    if user is None:
        return None
    return user.get("notion_page_id")


def invalidate_cache(tg_id: int = 0) -> None:
    """Сбросить кэш пользователя (или всех если tg_id=0)."""
    if tg_id:
        _user_cache.pop(tg_id, None)
    else:
        _user_cache.clear()
    logger.info("invalidate_cache: tg_id=%s", tg_id or "ALL")
