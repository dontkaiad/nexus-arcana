"""core/mcp_tasks.py — правила для создания и переноса задач из MCP (ADR-0039). Чистые функции, без обращений к базе.

Что тут живёт: проверка заголовка, даты, времени и напоминания, выбор категории и приоритета из справочников,
скользящий лимит «в сутки» в памяти процесса и тексты алертов. Маршруты (miniapp/backend/routes/mcp_tasks.py) только
собирают это вместе и пишут в базу.
"""
from __future__ import annotations

import html
import re
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Callable, Deque, Iterable, Optional

CREATE_LIMIT_PER_DAY = 20
RESCHEDULE_LIMIT_PER_DAY = 30
DAY_SECONDS = 86400.0

TITLE_MAX = 200
PRIO_EMOJI = ("🔴", "🟡", "⚪")
DEFAULT_PRIO = "🟡"
DEFAULT_CATEGORY = "Прочее"
# Имена (без эмодзи) категорий практики: не для задач, создаваемых из MCP.
FORBIDDEN_CATEGORY_NAMES = {"практика", "расходники"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_REMINDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f  ]")


class McpTaskError(Exception):
    """Отказ с честной причиной: status, короткий код и человекочитаемое сообщение (уходит в detail ответа)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


# ── поля ─────────────────────────────────────────────────────────────────────

def clean_title(raw: str) -> str:
    """1..200 символов после обрезки пробелов. Без управляющих символов и без < >: заголовок уходит в Telegram-сообщения
    с parse_mode=HTML (напоминание, дедлайн-пинг), а текст пришёл от модели: чужая разметка и ссылки там не нужны."""
    title = (raw or "").strip()
    if not 1 <= len(title) <= TITLE_MAX:
        raise McpTaskError(422, "bad_title", f"title must be 1..{TITLE_MAX} characters")
    if _CTRL_RE.search(title):
        raise McpTaskError(422, "bad_title", "title must not contain control characters or line breaks")
    if "<" in title or ">" in title:
        raise McpTaskError(422, "bad_title", "title must not contain < or >")
    return title


def parse_date(raw: str, field: str = "deadline_date") -> date:
    if not isinstance(raw, str) or not _DATE_RE.match(raw):
        raise McpTaskError(422, "bad_date", f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise McpTaskError(422, "bad_date", f"{field} is not a real calendar date")


def parse_time(raw: Optional[str], field: str = "deadline_time") -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not _TIME_RE.match(raw):
        raise McpTaskError(422, "bad_time", f"{field} must be HH:MM (00:00..23:59)")
    return raw


def parse_reminder(raw: Optional[str]) -> Optional[datetime]:
    """Локальное время владельца 'YYYY-MM-DDTHH:MM', без пояса и без секунд."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not _REMINDER_RE.match(raw):
        raise McpTaskError(422, "bad_reminder", "reminder must be YYYY-MM-DDTHH:MM (local time)")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise McpTaskError(422, "bad_reminder", "reminder is not a real date and time")


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                                   # 29 февраля → 28 февраля
        return d.replace(year=d.year + years, day=28)


def check_date_window(d: date, today: date) -> None:
    """Срок только в окне [вчера; сегодня + 2 года] по календарю владельца."""
    low, high = today - timedelta(days=1), add_years(today, 2)
    if not low <= d <= high:
        raise McpTaskError(422, "date_out_of_window",
                           f"deadline_date must be between {low.isoformat()} and {high.isoformat()}")


def deadline_end(d: date, hhmm: Optional[str]) -> datetime:
    """Момент срока для сравнения с напоминанием: с временем, иначе конец этого дня (срок «на весь день»)."""
    if hhmm:
        h, m = hhmm.split(":")
        return datetime(d.year, d.month, d.day, int(h), int(m))
    return datetime(d.year, d.month, d.day, 23, 59, 59)


def check_reminder(rem: datetime, d: date, hhmm: Optional[str], now_local: datetime, *, must_be_future: bool) -> None:
    """Напоминание не позже срока. Новое (пришло в запросе) ещё и не в прошлом: прошедшее планировщик либо молча пропустит,
    либо (в пределах двух минут) отправит сразу."""
    if rem > deadline_end(d, hhmm):
        raise McpTaskError(422, "reminder_after_deadline", "reminder must not be later than the deadline")
    if must_be_future and rem <= now_local:
        raise McpTaskError(422, "reminder_in_the_past", "reminder must be in the future")


def _name_of(code: str) -> str:
    """'💳 Прочее' → 'прочее' (без эмодзи, нижний регистр)."""
    return re.sub(r"^[^\w]+", "", code or "", flags=re.UNICODE).replace("️", "").strip().lower()


def pick_category(raw: str, codes: Iterable[str]) -> str:
    """Имя или полный код из справочника задач; «Практика» и «Расходники» нельзя; незнакомое — 422."""
    wanted = _name_of(raw or "")                      # умолчание («Прочее») задаёт тело запроса; пустая строка не угадывается
    if wanted in FORBIDDEN_CATEGORY_NAMES:
        raise McpTaskError(422, "forbidden_category", "categories Практика and Расходники are not allowed")
    for code in codes:
        if _name_of(code) == wanted and wanted not in FORBIDDEN_CATEGORY_NAMES:
            return code
    raise McpTaskError(422, "bad_category", "unknown category")


def pick_priority(emoji: str, codes: Iterable[str]) -> str:
    """🔴 🟡 ⚪ (⚪ с селектором вариации или без него: в справочнике он с селектором)."""
    e = (emoji or "").replace("\ufe0f", "")
    if e not in PRIO_EMOJI:
        raise McpTaskError(422, "bad_prio", "prio must be one of 🔴 🟡 ⚪")
    for code in codes:
        if code.replace("\ufe0f", "").startswith(e):
            return code
    raise McpTaskError(422, "bad_prio", "priority is not in the task priority list")


# ── лимит «в сутки» ──────────────────────────────────────────────────────────

class DailyLimiter:
    """Скользящее окно 24 часа в памяти процесса (после рестарта счётчик обнуляется). reserve() занимает место и возвращает
    0, либо число секунд до освобождения; release() возвращает место, если запись не удалась."""

    def __init__(self, limit: int, window: float = DAY_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: Deque[float] = deque()

    def _trim(self, now: float) -> None:
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()

    def reserve(self) -> int:
        now = self._clock()
        self._trim(now)
        if len(self._hits) >= self.limit:
            return max(1, int(self.window - (now - self._hits[0])) + 1)
        self._hits.append(now)
        return 0

    def release(self) -> None:
        if self._hits:
            self._hits.pop()

    def used(self) -> int:
        self._trim(self._clock())
        return len(self._hits)


# ── тексты алертов ───────────────────────────────────────────────────────────

def when_text(d: Optional[str], hhmm: Optional[str]) -> str:
    if not d:
        return "без срока"
    return f"{d} {hhmm}" if hhmm else d


def alert_created(title: str, d: str, hhmm: Optional[str]) -> str:
    return f"🤖 Claude создал задачу: <b>{html.escape(title)}</b>, срок {html.escape(when_text(d, hhmm))}"


def alert_rescheduled(task_id: str, title: str, old: tuple, new: tuple) -> str:
    return (f"🤖 Claude перенёс задачу #{html.escape(str(task_id))} <b>{html.escape(title)}</b>: "
            f"было {html.escape(when_text(*old))}, стало {html.escape(when_text(*new))}")
