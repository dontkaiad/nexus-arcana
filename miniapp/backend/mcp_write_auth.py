"""miniapp/backend/mcp_write_auth.py — запись для своего MCP-сервера kai-mcp (ADR-0039, по образцу mcp_read_auth).

kai-mcp ходит в `POST /api/mcp/tasks` (создать задачу) и `POST /api/mcp/tasks/{id}/reschedule` (перенести срок) с
`Authorization: Bearer <MCP_WRITE_TOKEN>`. Шлюз `mcp_write_gate` подключён ТОЛЬКО к этим двум маршрутам. В отличие от шлюзов
чтения здесь Bearer ОБЯЗАТЕЛЕН: запись по initData или cookie на этих маршрутах не открывается вообще (их нет в Mini App).
  • нет заголовка Authorization, другая схема или не Bearer → 401;
  • Bearer с неверным токеном → 401 и неверная попытка в окне по IP: больше 10 за 60 секунд → 429 (и верному токену тоже,
    пока окно не освободится);
  • верный токен → запрос идёт от имени MCP_WRITE_OWNER_TG_ID (см. auth.current_user_id).
Токен записи не равен токену чтения и любому другому служебному токену: иначе шлюз выключен (401). Токен чтения шлюз записи
не открывает (сравнение идёт только с токеном записи), токен записи не открывает шлюзы чтения. Сравнение hmac.compare_digest,
токен только из окружения, в логи не попадает.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, Response

from core.config import config
from miniapp.backend.cats_read_auth import MIN_TOKEN_LEN, FailureLimiter, _bearer, client_ip

logger = logging.getLogger("miniapp.mcp_write_auth")

_limiter = FailureLimiter()


def _enabled() -> bool:
    tok = config.mcp_write_token or ""
    return (len(tok) >= MIN_TOKEN_LEN
            and tok not in {                                     # токены служебных шлюзов не взаимозаменяемы
                config.cats_read_token or "", config.sitter_lists_token or "", config.care_write_token or "",
                config.care_period_token or "", config.cats_write_token or "", config.booking_service_token or "",
                config.mcp_read_token or "",                    # запись не открывается токеном чтения
            }
            and bool(config.mcp_write_owner_tg_id)
            and config.mcp_write_owner_tg_id in config.allowed_ids)


async def mcp_write_gate(request: Request, response: Response,
                         authorization: Optional[str] = Header(None)) -> None:
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    wait = _limiter.retry_after(ip)
    if wait:
        logger.warning("mcp write: too many bad attempts from %s, retry in %ss", ip, wait)
        raise HTTPException(status_code=429, detail="too many failed attempts", headers={"Retry-After": str(wait)})
    token = _bearer(authorization or "")
    expected = (config.mcp_write_token or "") if _enabled() else ""
    ok = bool(token) and bool(expected) and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _limiter.record_failure(ip)
        logger.warning("mcp write: bad or missing bearer from %s (feature %s)", ip, "on" if _enabled() else "off")
        raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
    request.state.mcp_write_owner = int(config.mcp_write_owner_tg_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
