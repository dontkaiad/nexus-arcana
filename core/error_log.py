"""core/error_log.py — структурное логирование ошибок ботов.

Релокейт log_error из notion_client (Notion-removal). Пишет в Python-логгер
(journald на VPS) И, если включено в .env, зеркалит ошибку в общую TG-группу
логов через общий лог-бот — Nexus и Arcana в свои топики форума (per-bot).
Сигнатура и возврат (True) сохранены для всех вызывателей.
"""
from __future__ import annotations

import logging

from core import ops_alert
from core.config import config

_err_log = logging.getLogger("bot.errors")

# Запас под лимит Telegram (4096 символов на сообщение).
_TG_MAX = 3500


def _thread_for(bot_label: str) -> str:
    """message_thread_id топика по боту: Arcana → свой, Zarya → топик букинга (1182), иначе → Nexus."""
    return ops_alert.thread_for(ops_alert.bot_key(bot_label))


def _format_for_group(
    message: str, error_type: str, claude_response: str, traceback: str,
    bot_label: str, error_code: str, context: str,
) -> str:
    """Сообщение об ошибке для TG-группы в шаблоне ops-группы. БЕЗ пользовательских данных: `message`
    (текст пользователя) и `claude_response` (ответ модели) в алерт НЕ попадают, как и сам текст
    исключения; только тип ошибки, код, класс исключения, место в коде и контекст (имя обработчика).
    Полный текст остаётся в логах контейнера (его пишет log_error ниже)."""
    cls, where = ops_alert.tb_summary(traceback)
    title = (error_type or "error") + (f" · {error_code}" if error_code and error_code != "–" else "")
    body = " · ".join(p for p in (cls, where, (context or "")[:120]) if p)
    return ops_alert.format_alert(ops_alert.bot_key(bot_label), "🔴", "error", title, body)[:_TG_MAX]


async def log_error(
    message: str,
    error_type: str = "error",
    claude_response: str = "",
    traceback: str = "",
    bot_label: str = "☀️ Nexus",
    error_code: str = "–",
    context: str = "",
) -> bool:
    _err_log.error(
        'bot=%s type=%s code=%s context=%s message=%s claude=%s trace=%s',
        bot_label, error_type or "error", error_code or "–", context or "–",
        message[:500], claude_response[:200] or "–", traceback[:500] or "–",
    )
    # Зеркалим в общую TG-группу логов (если включено в .env). Лениво
    # импортим sender, чтобы избежать циклов импорта; sender сам no-op'ит
    # когда TG_LOG_BOT_TOKEN/TG_LOG_CHAT_ID пусты. Логирование ошибки не должно
    # само бросать — оборачиваем в широкий guard.
    try:
        from core.bot_notify import notify_log_group
        await notify_log_group(
            _format_for_group(
                message, error_type, claude_response, traceback,
                bot_label, error_code, context,
            ),
            _thread_for(bot_label),
        )
    except Exception:
        pass
    return True
