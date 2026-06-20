"""core/bot_notify.py — отправка подтверждающих уведомлений в бота.

Используется Mini App backend'ом: действие в мини-аппе (отметить задачу
выполненной, создать, отменить и т.п.) даёт такой же отклик в чате бота,
как если бы это сделали прямо в боте.

Шлём через Telegram Bot API sendMessage напрямую (httpx), потому что
у backend'а нет инстанса aiogram-бота. Для приватного чата chat_id == tg_id.
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from html import escape as _esc

import httpx

from core.config import config

logger = logging.getLogger("bot_notify")

_API = "https://api.telegram.org/bot{token}/sendMessage"
_EDIT_API = "https://api.telegram.org/bot{token}/editMessageText"


def _token_for(bot: str) -> str:
    if bot == "arcana":
        return config.arcana.tg_token or ""
    return config.nexus.tg_token or ""


async def notify_user(tg_id: int, text: str, bot: str = "nexus") -> bool:
    """Отправить text в чат tg_id от имени бота (nexus|arcana).

    Никогда не бросает: ошибка отправки не должна валить write-действие
    мини-аппы. Возвращает True если Telegram принял сообщение.
    """
    token = _token_for(bot)
    if not token:
        logger.warning("notify_user: no token for bot=%s", bot)
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                _API.format(token=token),
                json={
                    "chat_id": tg_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code != 200:
            logger.warning("notify_user: %s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("notify_user failed: %s", e)
        return False


async def notify_log_group(text: str, thread_id: str = "") -> bool:
    """Отправить text в общую TG-группу логов через ОБЩИЙ лог-бот.

    Токен лог-бота (TG_LOG_BOT_TOKEN) и id группы (TG_LOG_CHAT_ID) берутся из .env.
    Если что-то из них пусто — фича выключена: тихо возвращаем False, прод
    работает как раньше. thread_id — message_thread_id топика форума (per-bot);
    пусто → шлём без топика.

    Никогда не бросает: сбой отправки лога не должен валить обработку ошибки.
    Возвращает True если Telegram принял сообщение.
    """
    token = config.log_bot_token
    chat_id = config.log_chat_id
    if not token or not chat_id:
        return False
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except (TypeError, ValueError):
            pass
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(_API.format(token=token), json=payload)
        if resp.status_code != 200:
            logger.warning("notify_log_group: %s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("notify_log_group failed: %s", e)
        return False


async def notify_startup(bot: str) -> bool:
    """Стартовый пинг в общую TG-группу логов (как klgpff «поднялся»).

    Текст и топик форума выбираются по bot ('nexus'|'arcana') — каждый бот
    в свой топик. Fail-safe: переиспользует notify_log_group, который сам
    no-op'ит без TG_LOG_BOT_TOKEN/TG_LOG_CHAT_ID, поэтому старт бота НЕ падает,
    если лог-группа не настроена. Старт важнее лога.
    """
    if bot == "arcana":
        label, thread = "🌒 <b>Arcana</b>", config.log_thread_arcana
    else:
        label, thread = "☀️ <b>Nexus</b>", config.log_thread_nexus
    try:
        host = socket.gethostname()
    except Exception:
        host = "?"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = f"{label} поднялся\n<code>{_esc(host)}</code> · {now}"
    return await notify_log_group(text, thread)


async def clear_task_reminder(task_id: str, bot: str = "nexus") -> bool:
    """Погасить живую плашку-напоминание задачи в чате.

    Меняет текст на «✅ <задача> — отмечено в приложении» и убирает кнопки
    (editMessageText без reply_markup снимает inline-клавиатуру). Вызывается
    когда задачу отметили не кнопкой в чате, а в Mini App.

    Никогда не бросает. Сообщение старше 48ч Telegram редактировать не даёт —
    тихо удаляем строку. Возвращает True если плашку отредактировали.
    """
    from core.task_reminder_msg import get_task_reminder, delete_task_reminder
    row = await get_task_reminder(task_id)
    if not row:
        return False
    token = _token_for(bot)
    if not token:
        await delete_task_reminder(task_id)
        return False
    title = (row.get("title") or "").strip()
    text = f"✅ <b>{_esc(title)}</b> — отмечено в приложении" if title else "✅ Отмечено в приложении"
    edited = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                _EDIT_API.format(token=token),
                json={
                    "chat_id": row["chat_id"],
                    "message_id": row["message_id"],
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
        if resp.status_code != 200:
            logger.info("clear_task_reminder: edit skipped %s %s", resp.status_code, resp.text[:150])
        else:
            edited = True
    except Exception as e:
        logger.warning("clear_task_reminder failed: %s", e)
    await delete_task_reminder(task_id)
    return edited
