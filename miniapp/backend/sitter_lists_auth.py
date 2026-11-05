"""miniapp/backend/sitter_lists_auth.py — служебное чтение списков «для ситтера» по Bearer-токену.

Приложение heylark_cats ходит в `GET /api/sitter/lists` с `Authorization: Bearer <SITTER_LISTS_TOKEN>`.
Отличия от `cats_read_gate`:
  • ЖЁСТКИЙ шлюз: заголовка нет или токен неверный → 401, на initData / cookie не «проваливаемся»;
  • токен ОТДЕЛЬНЫЙ и не взаимозаменяем: токен котов сюда не подходит, токен ситтера не открывает ни /api/cats,
    ни другие маршруты (он нигде больше не проверяется); если оба токена совпали — доступ выключен целиком;
  • собственное окно неверных попыток (10 за 60 секунд на IP → 429), счётчики с токеном котов не смешиваются;
  • шлюз ничего не кладёт в request.state и не влияет на `current_user_id`.
Сравнение — hmac.compare_digest, токен только из окружения, в логи не попадает.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, Response

from core.config import config
from miniapp.backend.cats_read_auth import MIN_TOKEN_LEN, FailureLimiter, _bearer, client_ip

logger = logging.getLogger("miniapp.sitter_lists_auth")

_limiter = FailureLimiter()


def _enabled() -> bool:
    tok = config.sitter_lists_token or ""
    return (len(tok) >= MIN_TOKEN_LEN
            and tok != (config.cats_read_token or "")             # токены не должны совпадать
            and tok != (config.care_write_token or "")
            and bool(config.sitter_lists_owner_tg_id)
            and config.sitter_lists_owner_tg_id in config.allowed_ids)


async def sitter_lists_gate(request: Request, response: Response,
                            authorization: Optional[str] = Header(None)) -> int:
    """Возвращает tg_id владельца, чьи списки отдаются. Любой сбой — 401/429, без обходных путей."""
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    wait = _limiter.retry_after(ip)
    if wait:
        logger.warning("sitter lists: too many bad attempts from %s, retry in %ss", ip, wait)
        raise HTTPException(status_code=429, detail="too many failed attempts", headers={"Retry-After": str(wait)})
    token = _bearer(authorization or "")
    expected = (config.sitter_lists_token or "") if _enabled() else ""
    ok = bool(token) and bool(expected) and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _limiter.record_failure(ip)
        logger.warning("sitter lists: bad or missing bearer from %s (feature %s)", ip, "on" if _enabled() else "off")
        raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return int(config.sitter_lists_owner_tg_id)
