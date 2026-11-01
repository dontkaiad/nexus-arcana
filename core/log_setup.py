"""core/log_setup.py — настройка шумных/опасных сторонних логгеров.

httpx на INFO печатает полный URL запроса, а у Telegram Bot API токен бота
лежит в пути (`/bot<TOKEN>/sendMessage`) — токен утекал в логи контейнера и
в app.log. Поднимаем httpx/httpcore (и сопутствующие HTTP-клиенты) до WARNING.
"""
from __future__ import annotations

import logging

_QUIET_LOGGERS = ("httpx", "httpcore", "urllib3", "telegram", "aiohttp.client")


def quiet_http_loggers(level: int = logging.WARNING) -> None:
    """Вызывать после logging.basicConfig в точке входа каждого бота."""
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(level)
