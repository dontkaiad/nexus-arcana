"""zarya/bot.py — ⭐ Zarya entrypoint (@heylark_booking_bot). #23 / ADR-0026."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from core.config import config
from zarya.handlers import RoleMiddleware, router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("zarya.bot")


async def _on_startup() -> None:
    try:
        from core.heartbeat import start_heartbeat
        start_heartbeat()
    except Exception as e:
        logger.warning("heartbeat start failed: %s", e)
    try:
        from core.bot_notify import notify_startup
        await notify_startup("zarya")
    except Exception as e:
        logger.warning("startup ping failed: %s", e)


async def main() -> None:
    token = config.booking_bot_token
    if not token:
        logger.error("BOOKING_BOT_TOKEN not set — Zarya cannot start")
        return
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.middleware(RoleMiddleware())
    dp.callback_query.middleware(RoleMiddleware())
    dp.include_router(router)
    dp.startup.register(_on_startup)
    logger.info("⭐ Zarya starting")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
