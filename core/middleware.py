"""core/middleware.py — Whitelist по Telegram ID + проверка базы Пользователей."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from core.config import config

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """
    Двухслойная проверка:
    1. Whitelist (allowed_ids) — быстрый gate
    2. База Пользователей в Notion — права и user_notion_id
    require_feature: если задано, проверяет checkbox у пользователя (arcana/nexus/finance)
    """

    def __init__(self, require_feature: str = "") -> None:
        self.require_feature = require_feature
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return

        # Слой 1: быстрый gate по whitelist
        if user.id not in config.allowed_ids:
            logger.warning("Blocked TG ID: %s (@%s, %s %s)", user.id, user.username, user.first_name, user.last_name or "")
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён.")
            return

        # Слой 2: проверка базы Пользователей
        try:
            from core.user_manager import get_user
            user_data = await get_user(user.id)
        except Exception as e:
            logger.error("middleware: get_user error: %s", e)
            user_data = None

        if user_data is None:
            logger.warning("User not in DB: %s", user.id)
            # Игнорируем без ответа
            return

        # Проверка feature-права (например для Arcana)
        if self.require_feature:
            has_access = user_data.get("permissions", {}).get(self.require_feature, False)
            if not has_access:
                if isinstance(event, Message):
                    feature_labels = {
                        "arcana":    "Arcana 🌒",
                        "finance":   "Финансы 💰",
                        "nexus":     "Nexus ☀️",
                    }
                    label = feature_labels.get(self.require_feature, self.require_feature)
                    await event.answer(f"⛔ Нет доступа к {label}")
                return

        # Прикрепляем данные пользователя к data для хэндлеров
        data["user_notion_id"] = user_data.get("notion_page_id", "")
        data["user_data"] = user_data

        return await handler(event, data)
