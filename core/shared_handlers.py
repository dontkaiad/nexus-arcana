"""Общие хендлеры для Nexus и Arcana."""
import os
from aiogram.types import Message


async def get_user_tz(tg_id: int) -> int:
    """Получить timezone offset пользователя из Памяти.
    Ключ в базе: tz_{tg_id}. Возвращает offset в часах (default: 3 для МСК).
    """
    from core.notion_client import memory_get
    stored = await memory_get(f"tz_{tg_id}")
    if stored:
        try:
            return int(stored)
        except Exception:
            pass
    return 3


async def handle_tz_command(message: Message, user_notion_id: str = "") -> None:
    """Команда /tz — одинакова для Nexus и Arcana.
    /tz UTC+5 или /tz Екатеринбург
    """
    from nexus.handlers.tasks import _update_user_tz
    text = (message.text or "").replace("/tz", "").strip()
    await _update_user_tz(message, text)
