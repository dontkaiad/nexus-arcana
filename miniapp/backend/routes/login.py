"""miniapp/backend/routes/login.py — единый вход heylark.dev для Nexus × Arcana.

nexus-arcana НЕ хостит свой Telegram Login Widget (виджет одного бота
верифицируется Telegram только для одного домена /setdomain — уже занят
под login.heylark.dev). Вместо этого:

  GET  /login   → редирект на login.heylark.dev/login?next=<исходный URL>.
                  Тот сервис выписывает hl_session на Domain=.heylark.dev
                  и редиректит браузер обратно на next — cookie уже видна
                  здесь без какого-либо callback на этой стороне.
  POST /logout  → чистит hl_session локально (домен общий, так что это
                  разлогинивает и остальные *.heylark.dev приложения).

Владелец-check (кто в ALLOWED_TELEGRAM_IDS) — отдельный слой, см.
miniapp/backend/auth.py и app.py (require_session_page).
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.config import config
from miniapp.backend import tg_auth

router = APIRouter(include_in_schema=False)


def is_safe_next(next_url: str) -> bool:
    """Разрешаем редирект только на относительный путь или *.heylark.dev по https."""
    if not next_url:
        return False
    if next_url.startswith("/") and not next_url.startswith("//"):
        return True
    try:
        p = urlparse(next_url)
        return p.scheme == "https" and (
            p.netloc == "heylark.dev" or p.netloc.endswith(".heylark.dev")
        )
    except Exception:
        return False


@router.get("/login")
async def login_redirect(request: Request, next: str = "/"):
    target = next if is_safe_next(next) else "/"
    absolute_next = str(request.base_url).rstrip("/") + target
    return RedirectResponse(
        f"{config.login_base_url}/login?next={quote(absolute_next, safe='')}",
        status_code=307,
    )


@router.post("/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        key=tg_auth.SESSION_COOKIE,
        domain=config.cookie_domain,
        path="/",
    )
    return response
