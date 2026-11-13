"""miniapp/backend/mcp_read_auth.py — чтение для своего MCP-сервера kai-mcp (по образцу ADR-0033, ADR-0038).

kai-mcp ходит в `GET /api/tasks` и `GET /api/arcana/reminders` с `Authorization: Bearer <MCP_READ_TOKEN>`. Шлюз
`mcp_read_gate` подключён ТОЛЬКО к этим двум маршрутам (запись, admin, финансы, клиенты, коты по этому токену не
открываются: тест обходит все маршруты):
  • заголовка Authorization нет → ничего не делает, дальше обычный вход (initData / cookie hl_session);
  • схема заголовка НЕ Bearer (например `tma <initData>`) → тоже ничего не делает: это не наш токен, запрос идёт
    обычным входом, а не получает 401 (отличие от cats_read_gate, где любая схема кроме Bearer даёт 401);
  • схема Bearer → токен обязан быть верным, иначе 401 (на другой способ входа не «проваливаемся»);
  • верный токен → запрос идёт от имени MCP_READ_OWNER_TG_ID (см. auth.current_user_id).
Сравнение — hmac.compare_digest. Токен только из окружения, в логи не попадает. Неверные попытки считаются по IP в
собственном окне (как у остальных служебных шлюзов): больше 10 за 60 секунд → 429.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, Response

from core.config import config
from miniapp.backend.cats_read_auth import MIN_TOKEN_LEN, FailureLimiter, _bearer, client_ip

logger = logging.getLogger("miniapp.mcp_read_auth")

_limiter = FailureLimiter()


def _enabled() -> bool:
    tok = config.mcp_read_token or ""
    return (len(tok) >= MIN_TOKEN_LEN
            and tok not in {                                     # токены служебных шлюзов не взаимозаменяемы
                config.cats_read_token or "", config.sitter_lists_token or "", config.care_write_token or "",
                config.care_period_token or "", config.cats_write_token or "", config.booking_service_token or "",
                config.mcp_write_token or "",          # токен чтения не равен токену записи (ADR-0039)
            }
            and bool(config.mcp_read_owner_tg_id)
            and config.mcp_read_owner_tg_id in config.allowed_ids)


def _is_bearer(authorization: str) -> bool:
    return (authorization or "").strip().partition(" ")[0].lower() == "bearer"


async def mcp_read_gate(request: Request, response: Response,
                        authorization: Optional[str] = Header(None)) -> None:
    if authorization is None or not _is_bearer(authorization):
        return                                                 # обычный вход (initData / cookie / другая схема)
    ip = client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
    wait = _limiter.retry_after(ip)
    if wait:
        logger.warning("mcp read: too many bad bearer attempts from %s, retry in %ss", ip, wait)
        raise HTTPException(status_code=429, detail="too many failed attempts", headers={"Retry-After": str(wait)})
    token = _bearer(authorization)
    expected = (config.mcp_read_token or "") if _enabled() else ""
    ok = bool(token) and bool(expected) and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    if not ok:
        _limiter.record_failure(ip)
        logger.warning("mcp read: bad bearer from %s (feature %s)", ip, "on" if _enabled() else "off")
        raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
    request.state.mcp_read_owner = int(config.mcp_read_owner_tg_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
