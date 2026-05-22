"""core/bot_notify.py — отправка подтверждающих уведомлений в бота.

Используется Mini App backend'ом: действие в мини-аппе (отметить задачу
выполненной, создать, отменить и т.п.) даёт такой же отклик в чате бота,
как если бы это сделали прямо в боте.

Шлём через Telegram Bot API sendMessage напрямую (httpx), потому что
у backend'а нет инстанса aiogram-бота. Для приватного чата chat_id == tg_id.
"""
from __future__ import annotations

import logging

import httpx

from core.config import config

logger = logging.getLogger("bot_notify")

_API = "https://api.telegram.org/bot{token}/sendMessage"


def _token_for(bot: str) -> str:
    if bot == "arcana":
        return config.arcana.tg_token or ""
    return config.nexus.tg_token or ""


async def notify_user(tg_id: int, text: str, bot: str = "nexus") -> bool:
    """Отправить text в чат tg_id от имени бота (nexus|arcana).

    Никогда не бросает: ошибка отправки не должна валить write-действие
    мини-аппы. Возвращает True если Telegram принял сообщение.
    """
    token = _token_for(bot)
    if not token:
        logger.warning("notify_user: no token for bot=%s", bot)
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                _API.format(token=token),
                json={
                    "chat_id": tg_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code != 200:
            logger.warning("notify_user: %s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("notify_user failed: %s", e)
        return False
