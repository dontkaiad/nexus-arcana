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

        # Дебаг: показать все ключи и checkbox-значения для диагностики
        checkbox_fields = {k: v.get("checkbox") for k, v in props.items() if v.get("type") == "checkbox"}
        logger.info("get_user(%s): checkbox fields в базе: %s", tg_id, checkbox_fields)

        name_items = props.get("Имя", {}).get("title", [])
        name = name_items[0]["text"]["content"] if name_items else ""

        role_sel = props.get("Роль", {}).get("select") or {}
        role = role_sel.get("name", "")

        def _checkbox(primary: str, fallback: str = "") -> bool:
            """Читает checkbox поле, пробуя оба варианта имени (с эмодзи и без)."""
            val = props.get(primary, {}).get("checkbox", None)
            if val is not None:
                return val
            if fallback:
                return props.get(fallback, {}).get("checkbox", False)
            return False

        permissions = {
            "nexus":     _checkbox("☀️ Nexus",   "Nexus"),
            "arcana":    _checkbox("🌒 Arcana",  "Arcana"),
            "finance":   _checkbox("💰 Финансы", "Финансы"),
        }
        logger.info("get_user(%s): permissions resolved = %s", tg_id, permissions)

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
