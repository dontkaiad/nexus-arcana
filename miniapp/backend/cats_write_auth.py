"""miniapp/backend/cats_write_auth.py — здоровье котов (вес, визиты / прививки, курсы лекарств) по ОТДЕЛЬНОМУ Bearer-токену.

Токен открывает только маршруты /api/cat-health/* и действует от имени владельца CATS_WRITE_OWNER_TG_ID. Он не равен остальным токенам
(токен котов, читающий токен ситтера, запись ухода, период: совпадение любого выключает доступ у ОБОИХ), не открывает ничего вне
/api/cat-health/*, а остальные токены не открывают эти маршруты. Жёсткий шлюз: нет заголовка или неверный токен — 401, на initData / cookie не «проваливаемся».
Собственное окно неверных попыток (10 за 60 с на IP → 429). Сравнение hmac.compare_digest, токен в логи не попадает.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, Response

from core.config import config
from miniapp.backend.cats_read_auth import MIN_TOKEN_LEN, FailureLimiter, _bearer, client_ip

logger = logging.getLogger("miniapp.cats_write_auth")

_limiter = FailureLimiter()


def _enabled() -> bool:
    tok = config.cats_write_token or ""
    return (len(tok) >= MIN_TOKEN_LEN
            and tok != (config.cats_read_token or "")
            and tok != (config.sitter_lists_token or "")
            and tok != (config.care_period_token or "")
            and tok != (config.care_write_token or "")
            and bool(config.cats_write_owner_tg_id)
            and config.cats_write_owner_tg_id in config.allowed_ids)


async def cats_write_gate(request: Request, response: Response, authorization: Optional[str] = Header(None)) -> int:
    """Возвращает tg_id владельца, от чьего имени приложение читает и пишет здоровье котов. Любой сбой — 401/429."""
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    wait = _limiter.retry_after(ip)
    if wait:
        logger.warning("cats write: too many bad attempts from %s, retry in %ss", ip, wait)
        raise HTTPException(status_code=429, detail="too many failed attempts", headers={"Retry-After": str(wait)})
    token = _bearer(authorization or "")
    expected = (config.cats_write_token or "") if _enabled() else ""
    ok = bool(token) and bool(expected) and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _limiter.record_failure(ip)
        logger.warning("cats write: bad or missing bearer from %s (feature %s)", ip, "on" if _enabled() else "off")
        raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return int(config.cats_write_owner_tg_id)
