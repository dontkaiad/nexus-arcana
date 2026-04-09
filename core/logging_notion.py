"""core/logging_notion.py — Logging handler that mirrors ERROR+ to Notion ⚠️ Ошибки."""
from __future__ import annotations

import asyncio
import logging
import time
import traceback as _tb
from typing import Optional


logger = logging.getLogger(__name__)

# Debounce: don't log the same message more than once per 5 minutes
_DEBOUNCE_SEC = 300
_recent: dict[str, float] = {}  # message_key → last_sent_ts


def _debounce_key(record: logging.LogRecord) -> str:
    """Stable key: logger name + first 120 chars of message."""
    return f"{record.name}:{record.getMessage()[:120]}"


class NotionErrorHandler(logging.Handler):
    """Async logging handler: forwards ERROR/CRITICAL to Notion ⚠️ Ошибки.

    Debounces identical messages (same logger + first 120 chars) to at most
    once per 5 minutes to prevent spam from cyclic errors.
    """

    def __init__(self, bot_label: str = "☀️ Nexus", level: int = logging.ERROR):
        super().__init__(level)
        self.bot_label = bot_label

    def emit(self, record: logging.LogRecord) -> None:
        # Skip our own logger to avoid recursion
        if record.name == __name__ or record.name == "core.notion_client":
            return

        # Debounce
        key = _debounce_key(record)
        now = time.monotonic()
        last = _recent.get(key, 0)
        if now - last < _DEBOUNCE_SEC:
            return
        _recent[key] = now

        # Evict old entries (keep dict small)
        if len(_recent) > 500:
            cutoff = now - _DEBOUNCE_SEC
            to_del = [k for k, v in _recent.items() if v < cutoff]
            for k in to_del:
                del _recent[k]

        # Schedule async write without blocking the logger
        msg = record.getMessage()[:2000]
        tb_text = ""
        if record.exc_info and record.exc_info[2]:
            tb_text = "".join(_tb.format_exception(*record.exc_info))[:2000]

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop — can't send async

        loop.create_task(self._write_to_notion(msg, tb_text, record.name))

    async def _write_to_notion(self, message: str, tb_text: str, logger_name: str) -> None:
        try:
            from core.notion_client import log_error
            await log_error(
                message=message,
                error_type="logger_error",
                claude_response="",
                traceback=tb_text,
                bot_label=self.bot_label,
                error_code=logger_name[:30],
            )
        except Exception:
            pass  # never crash the bot because of logging


def install(bot_label: str = "☀️ Nexus") -> None:
    """Attach NotionErrorHandler to the root logger."""
    handler = NotionErrorHandler(bot_label=bot_label)
    logging.getLogger().addHandler(handler)
