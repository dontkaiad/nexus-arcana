"""Shared handler utilities."""
from __future__ import annotations

import logging
from aiogram.types import Message, CallbackQuery

logger = logging.getLogger("nexus.handlers")


async def react(msg: Message | CallbackQuery, emoji: str = "✅") -> None:
    """Safely set a reaction on a message. Silently ignores errors."""
    try:
        target = msg if isinstance(msg, Message) else msg.message
        if target:
            await target.react([{"type": "emoji", "emoji": emoji}])
    except Exception:
        pass  # Reactions may be unavailable (groups, old API, etc.)
