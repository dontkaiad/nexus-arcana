"""core/utils.py — общие утилиты для обоих ботов."""
from __future__ import annotations

import logging
from typing import Optional, Union

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    ReactionTypeEmoji,
)

logger = logging.getLogger("core.utils")


# ── Styled inline buttons (Telegram Bot API 9.4) ─────────────────────────────

def styled_button(
    text: str, callback_data: str, style: Optional[str] = None
) -> InlineKeyboardButton:
    """Создать inline-кнопку с опциональным стилем.

    style: "destructive" (красный), "secondary" (серый), None (дефолт).
    Передаётся в pydantic модель через extra='allow' — уходит в запрос
    как поле style. Если клиент Telegram/библиотека не поддерживают —
    игнорируется (кнопка просто синяя).
    """
    kwargs: dict = {}
    if style:
        kwargs["style"] = style
    return InlineKeyboardButton(text=text, callback_data=callback_data, **kwargs)


def cancel_button(
    text: str = "❌ Отмена", callback_data: str = "cancel"
) -> InlineKeyboardButton:
    """Красная кнопка отмены/удаления/отказа."""
    return styled_button(text, callback_data, "destructive")


def secondary_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """Серая кнопка второстепенного действия (правка, продолжить)."""
    return styled_button(text, callback_data, "secondary")


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
