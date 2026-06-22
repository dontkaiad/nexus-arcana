"""Общие хендлеры для Nexus и Arcana."""
from aiogram.types import Message


async def get_user_tz(tg_id: int) -> int:
    """Получить timezone offset пользователя (ключ tz_{tg_id}).

    Единый источник чтения — core.location.get_user_tz (#170): TTL-кеш +
    PgMemoryRepo. Default 3 (МСК).
    """
    from core.location import get_user_tz as _get
    return await _get(tg_id)


async def handle_tz_command(message: Message, user_notion_id: str = "") -> None:
    """Команда /tz — одинакова для Nexus и Arcana.
    /tz UTC+5 или /tz Екатеринбург
    """
    from nexus.handlers.tasks import _update_user_tz
    text = (message.text or "").replace("/tz", "").strip()
    await _update_user_tz(message, text, user_notion_id=user_notion_id)
