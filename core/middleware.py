"""core/middleware.py — Whitelist по Telegram ID"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from core.config import config

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return
        if user.id not in config.allowed_ids:
            logger.warning("Blocked TG ID: %s", user.id)
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён.")
            return
        return await handler(event, data)
