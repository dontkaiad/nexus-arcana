"""core/logging_notion.py — Debounced logging handler forwarding ERROR+ to stdout.

Дополнительно зеркалит непойманные исключения (которые всплыли до aiogram без
try/except и были залогированы на root-логгере) в общую TG-группу логов — в
тот же дебаунс-гейт, что и stdout, чтобы группу не залило при флуде. Осознанные
ошибки зеркалит сам core/error_log.log_error; это — мост для остального.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback as _tb


_err_log = logging.getLogger("bot.errors.system")

# Debounce: suppress identical messages for 5 minutes to avoid stdout spam
_DEBOUNCE_SEC = 300
_recent: dict = {}  # message_key → last_sent monotonic timestamp

# Strong refs на fire-and-forget задачи зеркалирования в TG — иначе asyncio
# может собрать задачу GC до завершения. done-callback снимает ссылку и
# «забирает» исключение, чтобы не плодить "Task exception was never retrieved".
_bg_tasks: set = set()


def _bg_done(task: "asyncio.Task") -> None:
    _bg_tasks.discard(task)
    try:
        task.exception()  # retrieve → silence unretrieved-exception warning
    except Exception:
        pass


def _fire_and_forget(coro) -> None:
    """Запустить async-корутину из sync-контекста (logging.emit).

    Нужен крутящийся event loop (aiogram). Нет loop → тихий скип: логирование
    важнее зеркала, краш недопустим. notify_log_group сам никогда не бросает.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()  # нет running loop — закрыть корутину, не планировать
        return
    task = loop.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_done)


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

        # Дебаунс: первое вхождение ключа пропускаем всегда (важно не потерять
        # первое появление новой ошибки), повтор в окне 5 мин — гасим. Раньше
        # дефолт .get(key, 0) глушил первое сообщение, если time.monotonic() был
        # меньше _DEBOUNCE_SEC (свежий clock сразу после старта).
        key = _debounce_key(record)
        now = time.monotonic()
        last = _recent.get(key)
        if last is not None and now - last < _DEBOUNCE_SEC:
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

        # Зеркалим непойманную ошибку в общую TG-группу логов (за дебаунс-гейтом
        # выше — не чаще раза в 5 мин на одинаковую ошибку). emit() синхронный,
        # notify_log_group async → fire-and-forget на running loop; нет loop →
        # тихий скип. notify_log_group сам no-op'ит без LOG_BOT_TOKEN. Всё в
        # guard: сбой зеркала не должен ломать логирование.
        try:
            from core.bot_notify import notify_log_group
            from core.error_log import _format_for_group, _thread_for
            text = _format_for_group(
                msg, "uncaught", "", tb_text, self.bot_label, "–", record.name,
            )
            _fire_and_forget(notify_log_group(text, _thread_for(self.bot_label)))
        except Exception:
            pass


# Backward-compat alias (was NotionErrorHandler)
NotionErrorHandler = StructuredErrorHandler


def install(bot_label: str = "☀️ Nexus") -> None:
    """Attach StructuredErrorHandler to the root logger."""
    handler = StructuredErrorHandler(bot_label=bot_label)
    logging.getLogger().addHandler(handler)
