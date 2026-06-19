"""core/error_log.py — структурное логирование ошибок ботов.

Релокейт log_error из notion_client (Notion-removal). Раньше писал в Notion
⚠️ Ошибки; теперь — только Python-логгер (Notion удалён). Сигнатура и
возврат (True) сохранены для всех вызывателей.
"""
from __future__ import annotations

import logging

_err_log = logging.getLogger("bot.errors")


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
    return True
