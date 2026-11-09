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
from core.log_setup import quiet_http_loggers  # noqa: E402

quiet_http_loggers()  # токен бота в URL httpx (#268)
logger = logging.getLogger("zarya.bot")


async def _on_startup(**kwargs) -> None:
    bot = kwargs.get("bot")
    try:
        from core.heartbeat import start_heartbeat
        start_heartbeat()
    except Exception as e:
        logger.warning("heartbeat start failed: %s", e)
    try:
        from zarya import scheduler
        scheduler.init(bot)
        await scheduler.restore_on_startup()
    except Exception as e:
        logger.warning("booking reminder restore failed: %s", e)
    try:
        from core.bot_notify import notify_startup
        await notify_startup("zarya")
    except Exception as e:
        logger.warning("startup ping failed: %s", e)


def _write_build_stamp() -> None:
    """Runtime version stamp (no Docker layer — see zarya/Dockerfile note).
    `core.bot_notify._version_stamp()` reads /app/BUILD_STAMP for the «поднялся» ping."""
    try:
        from datetime import datetime, timezone
        with open("/app/BUILD_STAMP", "w", encoding="utf-8") as f:
            f.write("started " + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))
    except Exception as e:
        logger.warning("build stamp write failed: %s", e)


def build_dispatcher() -> Dispatcher:
    """Диспетчер Зари с её роутером. Роль выставляется ВНЕШНИМ middleware: обычный (`.middleware`) в aiogram 3 выполняется ПОСЛЕ фильтров,
    и фильтры с параметром `role` (_is_trip, _is_quick_booking, _is_agenda) всегда видели бы значение по умолчанию «guest» и не срабатывали."""
    dp = Dispatcher()
    dp.message.outer_middleware(RoleMiddleware())
    dp.callback_query.outer_middleware(RoleMiddleware())
    dp.include_router(router)
    return dp


async def main() -> None:
    _write_build_stamp()
    token = config.booking_bot_token
    if not token:
        logger.error("BOOKING_BOT_TOKEN not set — Zarya cannot start")
        return
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = build_dispatcher()
    dp.startup.register(_on_startup)
    logger.info("⭐ Zarya starting")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
