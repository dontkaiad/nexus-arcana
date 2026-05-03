"""core/client_resolve.py — общий хелпер для всех handler'ов Аркана.

Объединяет find_or_create_client + announce-сообщение «🆕 Создала клиента»
с reply-mapping (чтобы Кай могла ответить «🌟»/«бесплатный» и сменить
тип уже созданного клиента через стандартный reply_update flow).

Используется в:
- arcana/handlers/sessions.py (single + multi flow)
- arcana/handlers/rituals.py
- arcana/handlers/work_preview.py
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram.types import Message

from core.message_pages import save_message_page
from core.notion_client import find_or_create_client

logger = logging.getLogger("core.client_resolve")

_CTYPE_LABEL = {
    "🤝 Платный": "🤝 Платный",
    "🎁 Бесплатный": "🎁 Бесплатный",
    "🌟 Self": "🌟 Self",
}


async def resolve_or_create(
    message: Message,
    name: str,
    *,
    user_notion_id: str = "",
    default_type: str = "🤝 Платный",
    announce: bool = True,
) -> Optional[str]:
    """Находит клиента по имени; если нет — создаёт + анонсирует Кай.

    Возвращает client_id или None при ошибке создания (caller должен решить
    что делать — обычно «не падать, продолжить как is_personal=False
    с client_id=None»).

    announce=True (default): шлёт «🆕 Создала клиента {name} (🤝 Платный) ·
    реплай чтобы сменить тип» и регистрирует mapping для reply_update.
    """
    if not name:
        return None
    cid, created = await find_or_create_client(
        name, user_notion_id=user_notion_id, default_type=default_type,
    )
    if not cid:
        return None
    if created and announce:
        label = _CTYPE_LABEL.get(default_type, default_type)
        try:
            sent = await message.answer(
                f"🆕 Создала клиента <b>{name}</b> ({label})\n"
                "<i>↩️ Реплай: «🌟», «🎁», «бесплатный» — сменить тип</i>",
                parse_mode="HTML",
            )
            await save_message_page(
                chat_id=sent.chat.id,
                message_id=sent.message_id,
                page_id=cid,
                page_type="client",
                bot="arcana",
            )
        except Exception as e:
            logger.warning("announce new client failed: %s", e)
    return cid
