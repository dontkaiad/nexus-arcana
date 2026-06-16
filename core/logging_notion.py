"""core/logging_notion.py — Debounced logging handler forwarding ERROR+ to stdout."""
from __future__ import annotations

import logging
import time
import traceback as _tb


_err_log = logging.getLogger("bot.errors.system")

# Debounce: suppress identical messages for 5 minutes to avoid stdout spam
_DEBOUNCE_SEC = 300
_recent: dict = {}  # message_key → last_sent monotonic timestamp


def _debounce_key(record: logging.LogRecord) -> str:
    """Stable key: logger name + first 120 chars of message."""
    return f"{record.name}:{record.getMessage()[:120]}"


class StructuredErrorHandler(logging.Handler):
    """Debounced handler: forwards root-logger ERROR/CRITICAL to bot.errors.system.

    Prevents identical messages from flooding stdout more than once per 5 minutes.
    journald on VPS will pick up the structured output via stdout.
    """

    def __init__(self, bot_label: str = "☀️ Nexus", level: int = logging.ERROR):
        super().__init__(level)
        self.bot_label = bot_label

    def emit(self, record: logging.LogRecord) -> None:
        # Avoid recursion: don't re-process our own output
        if record.name.startswith("bot.errors"):
            return

        key = _debounce_key(record)
        now = time.monotonic()
        if now - _recent.get(key, 0) < _DEBOUNCE_SEC:
            return
        _recent[key] = now

        # Evict stale entries (keep dict bounded)
        if len(_recent) > 500:
            cutoff = now - _DEBOUNCE_SEC
            for k in [k for k, v in _recent.items() if v < cutoff]:
                del _recent[k]

        msg = record.getMessage()[:2000]
        tb_text = ""
        if record.exc_info and record.exc_info[2]:
            tb_text = "".join(_tb.format_exception(*record.exc_info))[:500]

        _err_log.error(
            'bot=%s source=%s message=%s trace=%s',
            self.bot_label, record.name, msg, tb_text or "–",
        )


# Backward-compat alias (was NotionErrorHandler)
NotionErrorHandler = StructuredErrorHandler


def install(bot_label: str = "☀️ Nexus") -> None:
    """Attach StructuredErrorHandler to the root logger."""
    handler = StructuredErrorHandler(bot_label=bot_label)
    logging.getLogger().addHandler(handler)
