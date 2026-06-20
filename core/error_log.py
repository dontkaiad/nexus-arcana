"""core/error_log.py — структурное логирование ошибок ботов.

Релокейт log_error из notion_client (Notion-removal). Пишет в Python-логгер
(journald на VPS) И, если включено в .env, зеркалит ошибку в общую TG-группу
логов через общий лог-бот — Nexus и Arcana в свои топики форума (per-bot).
Сигнатура и возврат (True) сохранены для всех вызывателей.
"""
from __future__ import annotations

import logging
from html import escape as _esc

from core.config import config

_err_log = logging.getLogger("bot.errors")

# Запас под лимит Telegram (4096 символов на сообщение).
_TG_MAX = 3500


def _thread_for(bot_label: str) -> str:
    """message_thread_id топика по боту: Arcana → свой, иначе → Nexus."""
    if "Arcana" in bot_label or "🌒" in bot_label:
        return config.log_thread_arcana
    return config.log_thread_nexus


def _format_for_group(
    message: str, error_type: str, claude_response: str, traceback: str,
    bot_label: str, error_code: str, context: str,
) -> str:
    """HTML-сообщение для TG-группы. Всё пользовательское — экранируем."""
    parts = [
        f"🐞 <b>{_esc(bot_label)}</b> · {_esc(error_type or 'error')} · "
        f"{_esc(error_code or '–')}"
    ]
    if context:
        parts.append(f"<i>{_esc(context[:200])}</i>")
    if message:
        parts.append(f"<b>msg:</b> {_esc(message[:800])}")
    if claude_response:
        parts.append(f"<b>claude:</b> {_esc(claude_response[:400])}")
    if traceback:
        parts.append(f"<pre>{_esc(traceback[:1500])}</pre>")
    return "\n".join(parts)[:_TG_MAX]


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
