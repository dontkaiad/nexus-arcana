"""core/llm_health.py — «ИИ недоступен»: ошибки billing / auth у Anthropic, уведомление владельцу, ответ пользователю.

Раньше при нулевом балансе API бот просто молчал (ask_claude глушит APIError и возвращает ""). Теперь:
  • ошибка billing («credit balance is too low») или auth (401/403) помечает ИИ недоступным (до первого успешного вызова);
  • владельцу — Telegram-уведомление на ВСЕ его аккаунты (allowed_ids), не чаще раза в 30 минут (метка в SQLite
    pending_kv, переживает рестарт);
  • пользователю — «🧠 ИИ временно недоступен, работают кнопки и Mini App» (core.middleware.LlmNoticeMiddleware):
    флаг обновления ставится в contextvar, middleware проверяет его после обработчика.
Транзиентные ошибки (429, 5xx, таймаут) сюда не попадают — ими занимается retry_transient.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import Optional

logger = logging.getLogger("core.llm_health")

USER_NOTICE = "🧠 ИИ временно недоступен, работают кнопки и Mini App"
ALERT_EVERY_SEC = 30 * 60
_KV_UID, _KV_KIND = 0, "llm_alert_sent"

_state: dict = {"kind": None, "since": 0.0}
_hit: contextvars.ContextVar = contextvars.ContextVar("llm_unavailable_hit", default=False)
_tasks: set = set()


def classify_error(exc: BaseException) -> Optional[str]:
    """'billing' | 'auth' | None. Billing у Anthropic приходит как 400 invalid_request «credit balance is too low»."""
    import anthropic
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if "credit balance" in msg or ("billing" in msg and status in (400, 402, 403)):
        return "billing"
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) or status in (401, 403):
        return "auth"
    return None


def unavailable() -> Optional[str]:
    return _state["kind"]


def begin_update() -> None:
    _hit.set(False)


def hit_in_update() -> bool:
    return bool(_hit.get())


def on_error(exc: BaseException) -> None:
    """Вызывается из claude_client при APIError. Никогда не бросает."""
    try:
        kind = classify_error(exc)
        if not kind:
            return
        if _state["kind"] != kind:
            _state.update(kind=kind, since=time.time())
        _hit.set(True)
        _schedule_alert(kind)
    except Exception:
        logger.warning("llm_health.on_error failed", exc_info=True)


def on_ok() -> None:
    if _state["kind"]:
        logger.warning("llm_health: ИИ снова доступен (был %s)", _state["kind"])
        _state.update(kind=None, since=0.0)


def _alert_text(kind: str) -> str:
    if kind == "billing":
        return ("⚠️ <b>Anthropic API: закончился баланс</b>\nБот работает без ИИ (кнопки и Mini App — как обычно). "
                "Пополни: console.anthropic.com → Plans &amp; Billing.")
    return ("⚠️ <b>Anthropic API: ключ отклонён (auth)</b>\nБот работает без ИИ. Проверь ANTHROPIC_API_KEY на сервере.")


def should_alert(now: Optional[float] = None) -> bool:
    """Не чаще раза в 30 минут; метка в pending_kv переживает рестарт процесса."""
    from core import pending_kv
    try:
        if pending_kv.has(_KV_UID, _KV_KIND, ttl=ALERT_EVERY_SEC):
            return False
        pending_kv.save(_KV_UID, _KV_KIND, {"t": now or time.time()}, ttl=ALERT_EVERY_SEC)
        return True
    except Exception:
        logger.warning("llm_health: pending_kv недоступен, шлём без дросселя", exc_info=True)
        return True


async def _send_alert(kind: str) -> None:
    from core.bot_notify import notify_user
    from core.config import config
    text = _alert_text(kind)
    for tg_id in config.allowed_ids:                 # все аккаунты владельца (см. память: два tg_id на один user_id)
        await notify_user(tg_id, text, bot="nexus")


def _schedule_alert(kind: str) -> None:
    if not should_alert():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    t = loop.create_task(_send_alert(kind))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
