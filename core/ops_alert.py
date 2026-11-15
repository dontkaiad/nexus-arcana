"""core/ops_alert.py — шаблон алертов ops-группы (тот же, что у ops/ops-notify.sh в heylark-infra).

Вид: «<эмодзи> <b>☀️ nexus</b> · <слово>», строка-заголовок, при необходимости строка курсивом;
parse_mode HTML. Транспорт прежний: общий лог-бот (TG_LOG_BOT_TOKEN), группа (TG_LOG_CHAT_ID), топик
форума по боту: Nexus → TG_LOG_THREAD_NEXUS (9), Arcana → TG_LOG_THREAD_ARCANA (10), Zarya →
TG_LOG_THREAD_BOOKING (1182), kai-mcp → TG_LOG_THREAD_KAIMCP (2219, иначе Nexus).

В ОБЫЧНЫЕ алерты об ошибках пользовательские данные не попадают: ни текст сообщения, ни ответ модели, ни
repr исключения (он несёт текст запроса). Только класс исключения, код ответа, место в коде и счётчики;
полный текст остаётся в логах контейнера (`docker logs`). Исключения, которые владелица решила оставить как
есть (транскрипты голосовых целиком, метки Claude в kai-mcp, строки аудита броней Зари), идут через
`format_event`: тот же шаблон, содержимое без изменений.
"""
from __future__ import annotations

import html
import os
import re
import time
import traceback as _tb
from typing import Dict, Optional, Tuple

from core.config import config

# ключ → (эмодзи проекта, имя) как в heylark-infra/ops/topics.env
LABELS: Dict[str, Tuple[str, str]] = {
    "nexus": ("☀️", "nexus"),
    "arcana": ("🌒", "arcana"),
    "zarya": ("⭐", "zarya"),
    "kai-mcp": ("🔌", "kai-mcp"),
}
MAX_TITLE = 1000
MAX_BODY = 2000
DEDUP_SECONDS = 60.0
_last_sent: Dict[str, float] = {}


def bot_key(bot_label: str) -> str:
    """'🌒 Arcana' → 'arcana', '⭐ Zarya' → 'zarya', всё остальное → 'nexus'."""
    s = (bot_label or "").lower()
    if "arcana" in s or "🌒" in s:
        return "arcana"
    if "zarya" in s or "booking" in s or "⭐" in s:
        return "zarya"
    return "nexus"


def thread_for(bot: str) -> str:
    """message_thread_id топика форума по ключу бота (пусто → без топика)."""
    if bot == "arcana":
        return config.log_thread_arcana
    if bot == "zarya":
        return config.log_thread_booking
    if bot == "kai-mcp":
        return config.log_thread_kaimcp or config.log_thread_nexus
    return config.log_thread_nexus


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _header(bot: str, emoji: str, word: str) -> str:
    e, name = LABELS.get(bot, LABELS["nexus"])
    return f"{emoji} <b>{e} {name}</b> · {word}"


def format_alert(bot: str, emoji: str, word: str, title: str = "", body: str = "") -> str:
    """Шапка, заголовок, курсивная строка. Заголовок и body экранируются (parse_mode HTML)."""
    msg = _header(bot, emoji, word)
    if title:
        msg += "\n" + html.escape(_cut(title, MAX_TITLE), quote=False)
    if body:
        msg += "\n<i>" + html.escape(_cut(body, MAX_BODY), quote=False) + "</i>"
    return msg


def format_event(bot: str, emoji: str, word: str, html_text: str) -> str:
    """Шапка шаблона + содержимое КАК ЕСТЬ (уже HTML-безопасное, экранирует вызывающий). Для событий,
    чей текст владелица решила не менять (транскрипты, метки Claude, аудит броней)."""
    return f"{_header(bot, emoji, word)}\n{html_text}"


# ---------- без пользовательского текста: класс, место, статус ----------

_CLASS_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Warning|Interrupt|Timeout|TimedOut|Cancelled)):")
_FRAME_RE = re.compile(r'File "[^"]*?([^/\\"]+)", line (\d+), in (\w+)')


def exc_label(exc: BaseException) -> str:
    """Класс исключения (и HTTP-статус, если есть). Без текста: в нём бывает запрос пользователя."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    return f"{name} (HTTP {status})" if isinstance(status, int) else name


def exc_where(exc: BaseException) -> str:
    try:
        frames = _tb.extract_tb(exc.__traceback__)
        if not frames:
            return ""
        f = frames[-1]
        return f"{os.path.basename(f.filename)}:{f.lineno} in {f.name}"
    except Exception:  # noqa: BLE001 — место в коде не стоит сбоя алерта
        return ""


def tb_summary(traceback_text: str) -> Tuple[str, str]:
    """(класс исключения, «файл:строка in функция») из ТЕКСТА трейсбэка. Сообщение исключения не берётся.
    Класс распознаётся только по строгому виду «…Error:» (текст пользователя вроде «Привет: мир» не пройдёт)."""
    cls = where = ""
    try:
        lines = [ln for ln in (traceback_text or "").strip().splitlines() if ln.strip()]
        if lines:
            m = _CLASS_RE.match(lines[-1].strip())
            if m:
                cls = m.group(1).rsplit(".", 1)[-1]
        frames = _FRAME_RE.findall(traceback_text or "")
        if frames:
            f, ln, fn = frames[-1]
            where = f"{f}:{ln} in {fn}"
    except Exception:  # noqa: BLE001
        pass
    return cls, where


# ---------- отправка ----------

async def send_alert(bot: str, emoji: str, word: str, title: str = "", body: str = "", *,
                     silent: bool = False) -> Optional[int]:
    """Один алерт по шаблону в топик бота. Не бросает. Одинаковый текст не чаще раза в DEDUP_SECONDS.
    Возвращает message_id или None (выключено / дубль / сбой)."""
    return await send_text(bot, format_alert(bot, emoji, word, title, body), silent=silent)


async def send_text(bot: str, text: str, *, silent: bool = False) -> Optional[int]:
    now = time.monotonic()
    prev = _last_sent.get(text)
    if prev is not None and now - prev < DEDUP_SECONDS:
        return None
    from core.bot_notify import _send_log

    mid = await _send_log(text, thread_for(bot), silent=silent)
    if mid is not None:
        _last_sent[text] = now
        if len(_last_sent) > 200:
            for k in [k for k, t in _last_sent.items() if now - t >= DEDUP_SECONDS]:
                _last_sent.pop(k, None)
    return mid
