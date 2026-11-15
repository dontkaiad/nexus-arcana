"""core/ops_transient.py — транзиентные сбои Telegram/сети: не алерт на каждую ошибку, а счётчик и один алерт на всплеск.

aiogram пишет в лог обрывы long polling («Failed to fetch updates - TelegramNetworkError …»): несколько раз
в час, ничего не ломается, бот переподключается сам. Раньше каждое такое событие зеркалилось в топик как
«uncaught». Теперь:
  * такая ошибка только считается (по классам, по суткам UTC) в /tmp/ops_transient.json; суточные счётчики
    читает «пульс» host-монитора (monitoring/monitor.sh в heylark-infra, контейнеры nexus-bot, arcana-bot,
    zarya-bot уже в его списке);
  * один алерт «Telegram API нестабилен», когда за 10 минут набралось 5 и больше;
  * «восстановлено», когда после этого прошло 10 тихих минут (tick() из heartbeat-цикла).
Никакого текста исключений: только имена классов и числа.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, deque
from typing import Callable, Deque, Optional, Tuple

COUNTERS_PATH = "/tmp/ops_transient.json"
WINDOW_SECONDS = 600.0
BURST_COUNT = 5
QUIET_SECONDS = 600.0

TRANSIENT_NAMES = frozenset({
    "TelegramNetworkError", "TelegramServerError", "TelegramRetryAfter",
    "ClientConnectorError", "ClientConnectionError", "ClientOSError", "ServerDisconnectedError",
    "ServerTimeoutError", "ConnectionResetError", "TimeoutError", "ReadTimeout", "ConnectTimeout",
    "ReadError", "ConnectError", "RemoteProtocolError", "PoolTimeout",
})
# наши баги / сломанная настройка: всегда алертим, даже если класс стоит под сетевым
NEVER_TRANSIENT = frozenset({
    "TelegramBadRequest", "TelegramForbiddenError", "TelegramUnauthorizedError",
    "TelegramConflictError", "TelegramNotFound", "TelegramMigrateToChat", "TelegramEntityTooLarge",
    "BadRequest", "Forbidden", "InvalidToken", "Conflict", "ChatMigrated",
})
TRANSIENT_MARKERS = ("Bad Gateway", "Request timeout error", "Read timed out")

Action = Tuple[str, str, str, str]      # (эмодзи, слово шаблона, заголовок, курсивная строка)


def is_transient(exc: BaseException) -> bool:
    if any(c.__name__ in NEVER_TRANSIENT for c in type(exc).__mro__):
        return False
    if any(c.__name__ in TRANSIENT_NAMES for c in type(exc).__mro__):
        return True
    return any(m in str(exc)[:300] for m in TRANSIENT_MARKERS)


def classify_record(record: logging.LogRecord) -> Optional[str]:
    """Имя класса транзиентной ошибки для записи лога, иначе None. Смотрит на исключение записи, а для
    aiogram («Failed to fetch updates - TelegramNetworkError: …», исключения в записи нет) на текст."""
    exc = record.exc_info[1] if record.exc_info else None
    if exc is not None:
        return type(exc).__name__ if is_transient(exc) else None
    if not record.name.startswith("aiogram"):
        return None
    msg = record.getMessage()[:300]
    if any(n in msg for n in NEVER_TRANSIENT):
        return None
    for n in sorted(TRANSIENT_NAMES):
        if n in msg:
            return n
    return "TelegramNetworkError" if any(m in msg for m in TRANSIENT_MARKERS) else None


def _top(counts: Counter, n: int = 3) -> str:
    return ", ".join(f"{name} ×{c}" for name, c in counts.most_common(n))


class TransientTracker:
    def __init__(self, path: str = COUNTERS_PATH, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.events: Deque = deque()
        self.alerted = False
        self.last_event = 0.0
        self.incident: Counter = Counter()
        self.day: Optional[str] = None
        self.counts: Counter = Counter()
        self.prev_day: Optional[str] = None
        self.prev_counts: Counter = Counter()
        self._load()

    @staticmethod
    def _today(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            self.day, self.prev_day = d.get("day"), d.get("prev_day")
            self.counts, self.prev_counts = Counter(d.get("counts") or {}), Counter(d.get("prev_counts") or {})
        except Exception:  # noqa: BLE001 — нет файла или битый: с нуля
            pass

    def _roll(self, now: float) -> bool:
        today = self._today(now)
        if self.day is None:
            self.day = today
            return False
        if today != self.day:
            self.prev_day, self.prev_counts = self.day, self.counts
            self.day, self.counts = today, Counter()
            return True
        return False

    def _save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"day": self.day, "counts": dict(self.counts),
                           "prev_day": self.prev_day, "prev_counts": dict(self.prev_counts)}, fh)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — счётчик не стоит сбоя бота
            pass

    def record(self, name: str) -> Optional[Action]:
        """Учесть транзиентную ошибку по имени класса. Возвращает алерт, когда всплеск впервые достиг порога."""
        now = self.clock()
        self._roll(now)
        self.counts[name] += 1
        self._save()
        self.events.append((now, name))
        while self.events and now - self.events[0][0] > WINDOW_SECONDS:
            self.events.popleft()
        self.last_event = now
        if self.alerted:
            self.incident[name] += 1
            return None
        if len(self.events) >= BURST_COUNT:
            self.alerted = True
            self.incident = Counter(n for _, n in self.events)
            return ("🟠", "warning", f"Telegram API нестабилен: {len(self.events)} сетевых ошибок за 10 мин",
                    _top(self.incident))
        return None

    def tick(self) -> Optional[Action]:
        """Из heartbeat-цикла. «Восстановлено» после QUIET_SECONDS тишины."""
        now = self.clock()
        if self._roll(now):
            self._save()
        if self.alerted and now - self.last_event >= QUIET_SECONDS:
            self.alerted = False
            total = sum(self.incident.values())
            top = _top(self.incident)
            self.incident = Counter()
            self.events.clear()
            return ("🟢", "recovered", f"Telegram/сеть снова в норме (тихо {int(QUIET_SECONDS // 60)} мин)",
                    f"за время сбоя {total} ошибок: {top}")
        return None


_tracker: Optional[TransientTracker] = None


def tracker() -> TransientTracker:
    global _tracker
    if _tracker is None:
        _tracker = TransientTracker()
    return _tracker
