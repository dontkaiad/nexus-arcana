"""core.utils.react должен отклонять эмодзи, которые Telegram не принимает
для реакций (REACTION_INVALID), вместо того чтобы бить по API вслепую.

Регресс: react(msg, "👂") падал с "Bad Request: REACTION_INVALID" —
👂 никогда не было в списке разрешённых Telegram эмодзи-реакций.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message

from core.utils import react


def _msg() -> Message:
    m = Message.model_construct(
        message_id=4609,
        date=datetime.now(timezone.utc),
        chat=Chat(id=67686090, type="private"),
    )
    fake_bot = MagicMock()
    fake_bot.set_message_reaction = AsyncMock()
    return m.as_(fake_bot)


@pytest.mark.asyncio
async def test_react_skips_unsupported_emoji_without_calling_telegram():
    msg = _msg()
    await react(msg, "👂")
    msg.bot.set_message_reaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_react_sends_supported_emoji():
    msg = _msg()
    await react(msg, "👀")
    msg.bot.set_message_reaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_react_accepts_variation_selector_form():
    """✍️ (U+FE0F variant) is how callers write it; must still pass."""
    msg = _msg()
    await react(msg, "✍️")
    msg.bot.set_message_reaction.assert_awaited_once()
