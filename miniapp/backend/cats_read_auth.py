"""miniapp/backend/cats_read_auth.py — служебное чтение карточек котов по Bearer-токену (ADR-0033).

Приложение heylark_cats ходит в `GET /api/cats` и `GET /api/cats/{id}` с `Authorization: Bearer <CATS_READ_TOKEN>`.
Шлюз `cats_read_gate` подключён ТОЛЬКО к этим двум маршрутам:
  • заголовка Authorization нет → ничего не делает, дальше обычный вход (initData / cookie hl_session);
  • заголовок есть → токен обязан быть верным, иначе 401 (на другой способ входа не «проваливаемся»);
  • верный токен → запрос идёт от имени владельца CATS_READ_OWNER_TG_ID (см. auth.current_user_id).
Сравнение — hmac.compare_digest. Токен только из окружения, в логи не попадает. Неверные попытки считаются по IP:
больше 10 за 60 секунд → 429 (в том числе для верного токена с этого IP, пока окно не освободится).
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import math
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from fastapi import Header, HTTPException, Request, Response

from core.config import config

logger = logging.getLogger("miniapp.cats_read_auth")

MIN_TOKEN_LEN = 32
FAIL_LIMIT = 10
FAIL_WINDOW = 60.0
MAX_TRACKED = 10_000


class FailureLimiter:
    """Скользящее окно неверных попыток по ключу (IP). Память ограничена: старые записи вычищаются."""

    def __init__(self, limit: int = FAIL_LIMIT, window: float = FAIL_WINDOW,
                 clock: Callable[[], float] = time.monotonic, max_tracked: int = MAX_TRACKED) -> None:
        self.limit, self.window, self._clock, self.max_tracked = limit, window, clock, max_tracked
        self._hits: Dict[str, Deque[float]] = {}

    def _prune(self, key: str, now: float) -> Deque[float]:
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
        return q

    def retry_after(self, key: str) -> int:
        """0 — можно; иначе сколько секунд ждать (целое, ≥ 1)."""
        now = self._clock()
        q = self._prune(key, now)
        if len(q) < self.limit:
            return 0
        return max(1, int(math.ceil(self.window - (now - q[0]))))

    def record_failure(self, key: str) -> None:
        now = self._clock()
        if key not in self._hits and len(self._hits) >= self.max_tracked:
            for k in list(self._hits):                         # сначала вычищаем протухшие
                self._prune(k, now)
            if len(self._hits) >= self.max_tracked:            # всё ещё много: выкидываем самую старую запись
                oldest = min(self._hits, key=lambda k: self._hits[k][-1])
                self._hits.pop(oldest, None)
        self._hits.setdefault(key, deque()).append(now)

    def reset(self) -> None:
        self._hits.clear()


_limiter = FailureLimiter()


def client_ip(peer: Optional[str], forwarded_for: Optional[str]) -> str:
    """IP клиента для лимита. Напрямую (публичный peer) — сам peer, заголовку не верим. Через прокси (Caddy в
    docker-сети: peer приватный) берём ПОСЛЕДНИЙ адрес X-Forwarded-For — его дописывает прокси, а не клиент."""
    if not peer:
        return "unknown"
    try:
        p = ipaddress.ip_address(peer)
    except ValueError:
        return peer                                            # не IP (например, тестовый клиент) — как есть
    if (p.is_private or p.is_loopback or p.is_link_local) and forwarded_for:
        last = forwarded_for.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            return peer
    return peer


def _enabled() -> bool:
    return (len(config.cats_read_token or "") >= MIN_TOKEN_LEN
            and bool(config.cats_read_owner_tg_id)
            and config.cats_read_owner_tg_id in config.allowed_ids)


def _bearer(authorization: str) -> Optional[str]:
    scheme, _, rest = (authorization or "").strip().partition(" ")
    token = rest.strip()
    return token if scheme.lower() == "bearer" and token else None


async def cats_read_gate(request: Request, response: Response,
                         authorization: Optional[str] = Header(None)) -> None:
    if authorization is None:
        return                                                 # обычный вход — initData / cookie, без изменений
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    wait = _limiter.retry_after(ip)
    if wait:
        logger.warning("cats read: too many bad bearer attempts from %s, retry in %ss", ip, wait)
        raise HTTPException(status_code=429, detail="too many failed attempts", headers={"Retry-After": str(wait)})
    token = _bearer(authorization)
    expected = (config.cats_read_token or "") if _enabled() else ""
    ok = bool(token) and bool(expected) and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _limiter.record_failure(ip)
        logger.warning("cats read: bad bearer from %s (feature %s)", ip, "on" if _enabled() else "off")
        raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
    request.state.cats_read_owner = int(config.cats_read_owner_tg_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
