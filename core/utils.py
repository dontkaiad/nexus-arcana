"""core/utils.py — общие утилиты для обоих ботов."""
from __future__ import annotations

import logging
from typing import Union

from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji

logger = logging.getLogger("core.utils")


async def react(msg: Union[Message, CallbackQuery], emoji: str = "✅") -> None:
    """Set a reaction on a message. Logs success/failure for diagnostics."""
    target = msg if isinstance(msg, Message) else getattr(msg, "message", None)
    if not target:
        logger.warning("react: no target message for emoji=%s", emoji)
        return
    try:
        await target.bot.set_message_reaction(
            chat_id=target.chat.id,
            message_id=target.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        logger.info(
            "react: OK chat=%s msg=%s emoji=%s",
            target.chat.id, target.message_id, emoji,
        )
    except Exception as e:
        logger.error(
            "react: FAILED chat=%s msg=%s emoji=%s error=%s",
            target.chat.id, target.message_id, emoji, e,
        )
