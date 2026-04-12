"""Фабрика тестовых ботов с реальными хэндлерами."""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Убедиться что проект в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from aiogram import Bot, Dispatcher
from aiogram.types import Update, Message, Chat, User, CallbackQuery
from aiogram.client.session.base import BaseSession
from datetime import datetime, timezone


class FakeSession(BaseSession):
    """Фейковая сессия — перехватывает все API вызовы к Telegram."""

    def __init__(self):
        super().__init__()
        self.responses: list[dict] = []
        self.reactions: list[dict] = []

    async def make_request(self, bot, method, timeout=None):
        """Перехватить все вызовы к Telegram API."""
        method_name = type(method).__name__

        if method_name == "SendMessage":
            self.responses.append({
                "type": "message",
                "text": method.text or "",
                "chat_id": method.chat_id,
                "reply_markup": str(method.reply_markup) if method.reply_markup else None,
                "parse_mode": method.parse_mode,
            })
            return Message(
                message_id=len(self.responses) + 100,
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )

        elif method_name == "EditMessageText":
            self.responses.append({
                "type": "edit",
                "text": method.text or "",
                "chat_id": method.chat_id,
            })
            return True

        elif method_name == "SetMessageReaction":
            self.reactions.append({
                "chat_id": method.chat_id,
                "message_id": method.message_id,
                "reaction": str(method.reaction),
            })
            return True

        elif method_name == "AnswerCallbackQuery":
            return True

        elif method_name == "DeleteMessage":
            return True

        elif method_name == "GetMe":
            return User(
                id=123456789,
                is_bot=True,
                first_name="TestBot",
                username="test_bot",
            )

        # Для всех остальных — True
        return True

    async def close(self):
        pass

    async def stream_content(self, url, headers=None, timeout=30,
                             chunk_size=65536, raise_for_status=True):
        """Фейковый stream — не используется в тестах."""
        yield b""

    def get_last_response(self) -> str:
        """Последний текстовый ответ бота."""
        if self.responses:
            return self.responses[-1].get("text", "")
        return ""

    def get_all_texts(self) -> list[str]:
        """Все текстовые ответы."""
        return [r.get("text", "") for r in self.responses]

    def has_buttons(self) -> bool:
        """Есть ли inline кнопки в последнем ответе."""
        if self.responses:
            return self.responses[-1].get("reply_markup") is not None
        return False

    def clear(self):
        """Очистить историю."""
        self.responses.clear()
        self.reactions.clear()


def make_update(text: str, user_id: int = 67686090,
                update_id: int = 1, message_id: int = 1) -> Update:
    """Создать фейковый Update с текстовым сообщением."""
    return Update(
        update_id=update_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(
                id=user_id,
                is_bot=False,
                first_name="Кай",
                language_code="ru",
            ),
            text=text,
        ),
    )


def make_callback_update(data: str, user_id: int = 67686090,
                         update_id: int = 1,
                         original_text: str = "") -> Update:
    """Создать фейковый Update с callback query."""
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id="test_cb_1",
            chat_instance="test",
            from_user=User(
                id=user_id,
                is_bot=False,
                first_name="Кай",
            ),
            message=Message(
                message_id=1,
                date=datetime.now(timezone.utc),
                chat=Chat(id=user_id, type="private"),
                text=original_text,
            ),
            data=data,
        ),
    )
