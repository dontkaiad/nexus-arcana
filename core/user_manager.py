"""core/user_manager.py — Управление пользователями через базу Пользователи в Notion."""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Кэш: {tg_id: {"notion_page_id": ..., "name": ..., "role": ..., "permissions": {...}, "_ts": ...}}
_user_cache: Dict[int, dict] = {}
_CACHE_TTL = 300  # 5 минут


async def get_user(tg_id: int) -> Optional[dict]:
    """Найти пользователя по TG ID в базе Пользователи. Вернуть None если не найден."""
    cached = _user_cache.get(tg_id)
    if cached and time.time() - cached.get("_ts", 0) < _CACHE_TTL:
        return cached

    from core.config import config
    from core.notion_client import query_pages

    db_id = config.db_users
    if not db_id:
        logger.warning("get_user: db_users not configured")
        return None

    try:
        results = await query_pages(
            db_id,
            filters={"property": "TG ID", "number": {"equals": tg_id}},
            page_size=5,  # берём 5 чтобы увидеть дубли
        )
        logger.info(
            "get_user(%s): Notion вернул %d записей: %s",
            tg_id,
            len(results),
            [
                {
                    "id": p["id"],
                    "name": (
                        (p.get("properties", {}).get("Имя", {}).get("title") or [{}])[0]
                        .get("text", {}).get("content", "?")
                    ),
                    "tg_id_field": (
                        p.get("properties", {}).get("TG ID", {}).get("number")
                    ),
                }
                for p in results
            ],
        )
        if not results:
            return None

        page = results[0]
        props = page.get("properties", {})
        logger.info("get_user(%s): используем page_id=%s", tg_id, page["id"])

        name_items = props.get("Имя", {}).get("title", [])
        name = name_items[0]["text"]["content"] if name_items else ""

        role_sel = props.get("Роль", {}).get("select") or {}
        role = role_sel.get("name", "")

        permissions = {
            "nexus":     props.get("Nexus",    {}).get("checkbox", False),
            "arcana":    props.get("Arcana",   {}).get("checkbox", False),
            "finance":   props.get("Финансы",  {}).get("checkbox", False),
            "passwords": props.get("Пароли",   {}).get("checkbox", False),
        }

        user_data = {
            "notion_page_id": page["id"],
            "name": name,
            "role": role,
            "permissions": permissions,
            "_ts": time.time(),
        }
        _user_cache[tg_id] = user_data
        return user_data

    except Exception as e:
        logger.error("get_user(%s) error: %s", tg_id, e)
        return None


async def check_permission(tg_id: int, feature: str) -> bool:
    """Проверить что у пользователя есть доступ к feature (nexus/arcana/finance/passwords)."""
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
