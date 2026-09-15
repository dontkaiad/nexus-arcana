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

# Telegram only accepts a fixed set of emoji for message reactions
# (Bot API "available reactions" list) — anything else raises
# REACTION_INVALID. Keep this in sync if Telegram extends the set.
VALID_REACTION_EMOJI = {
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢",
    "🎉", "🤩", "🤮", "💩", "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳",
    "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆", "💔", "🤨", "😐", "🍓",
    "🍾", "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃",
    "🙈", "😇", "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪",
    "🗿", "🆒", "💘", "🙉", "🦄", "😘", "💊", "🙊", "😎", "👾", "🤷‍♂",
    "🤷", "🤷‍♀", "😡",
}


# ── Styled inline buttons (Telegram Bot API 9.4) ─────────────────────────────

def styled_button(
    text: str, callback_data: str, style: Optional[str] = None
) -> InlineKeyboardButton:
    """Создать inline-кнопку.

    Bot API 9.4 декларирует поле `style` для InlineKeyboardButton, но
    реальные клиенты Telegram отвергают любую передачу этого поля
    ("invalid button style specified"). Параметр style принимаем для
    совместимости сигнатуры, но в Bot API не отправляем — кнопки
    рендерятся стандартными. Лучше серые кнопки, чем падающий бот.
    """
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def cancel_button(
    text: str = "❌ Отмена", callback_data: str = "cancel"
) -> InlineKeyboardButton:
    """Кнопка отмены/удаления/отказа."""
    return styled_button(text, callback_data, "destructive")


def secondary_button(text: str, callback_data: str) -> InlineKeyboardButton:
    """Кнопка второстепенного действия (правка, продолжить)."""
    return styled_button(text, callback_data, "secondary")


async def react(msg: Union[Message, CallbackQuery], emoji: str = "👀") -> None:
    """Set a reaction on a message. Logs success/failure for diagnostics."""
    target = msg if isinstance(msg, Message) else getattr(msg, "message", None)
    if not target:
        logger.warning("react: no target message for emoji=%s", emoji)
        return
    # Telegram rejects anything outside VALID_REACTION_EMOJI with
    # REACTION_INVALID — catch typos/unsupported emoji here instead of
    # spamming the API and logs with a request that can never succeed.
    if emoji.replace("️", "") not in VALID_REACTION_EMOJI:
        logger.warning(
            "react: unsupported emoji=%s (chat=%s msg=%s), skipping reaction",
            emoji, target.chat.id, target.message_id,
        )
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
