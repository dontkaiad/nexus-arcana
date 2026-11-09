"""miniapp/backend/care_write_auth.py — запись ситтера в расписание ухода по ОТДЕЛЬНОМУ Bearer-токену.

Токен открывает ровно два маршрута: POST /api/care/tasks/{id}/done и POST /api/care/tasks/{id}/postpone. Он не равен токену котов и
читающему токену ситтера (совпадение любого выключает доступ), не открывает чтение (/api/sitter/lists, /api/care/schedule, /api/cats),
а читающие токены не открывают запись. Жёсткий шлюз: нет заголовка или неверный токен — 401, на initData / cookie не «проваливаемся».
Собственное окно неверных попыток (10 за 60 с на IP → 429). Сравнение hmac.compare_digest, токен в логи не попадает.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, Response

from core.config import config
from miniapp.backend.cats_read_auth import MIN_TOKEN_LEN, FailureLimiter, _bearer, client_ip

logger = logging.getLogger("miniapp.care_write_auth")

_limiter = FailureLimiter()


def _enabled() -> bool:
    tok = config.care_write_token or ""
    return (len(tok) >= MIN_TOKEN_LEN
            and tok != (config.cats_read_token or "")
            and tok != (config.sitter_lists_token or "")
            and tok != (config.care_period_token or "")
            and bool(config.care_write_owner_tg_id)
            and config.care_write_owner_tg_id in config.allowed_ids)


async def care_write_gate(request: Request, response: Response, authorization: Optional[str] = Header(None)) -> int:
    """Возвращает tg_id владельца, от чьего имени ситтер выполняет цикл. Любой сбой — 401/429."""
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    wait = _limiter.retry_after(ip)
    if wait:
        logger.warning("care write: too many bad attempts from %s, retry in %ss", ip, wait)
        raise HTTPException(status_code=429, detail="too many failed attempts", headers={"Retry-After": str(wait)})
    token = _bearer(authorization or "")
    expected = (config.care_write_token or "") if _enabled() else ""
    ok = bool(token) and bool(expected) and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _limiter.record_failure(ip)
        logger.warning("care write: bad or missing bearer from %s (feature %s)", ip, "on" if _enabled() else "off")
        raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return int(config.care_write_owner_tg_id)
