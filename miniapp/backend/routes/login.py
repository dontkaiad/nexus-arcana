"""miniapp/backend/routes/login.py — Telegram Login Widget routes for Nexus × Arcana.

Provides browser-based SSO via hl_session cookie (cross-subdomain .heylark.dev).
Three routes (not under /api — HTML/redirect, not JSON):
  GET  /login           → login page (Jinja2)
  GET  /auth/callback   → validate widget payload, set cookie, redirect
  POST /logout          → clear cookie
"""
from __future__ import annotations

import logging
import pathlib
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core.config import config
from miniapp.backend import tg_auth

logger = logging.getLogger("miniapp.login")

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(include_in_schema=False)

_POST_LOGIN_PATH = "/"


def _is_safe_next(next_url: str) -> bool:
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = None, error: Optional[str] = None):
    bot_username = config.tg_login_bot_username
    callback_url = str(request.url_for("auth_callback"))
    if next and _is_safe_next(next):
        callback_url = callback_url + f"?next={next}"
    next_label = "Nexus × Arcana" if next else None
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "bot_username": bot_username,
            "callback_url": callback_url,
            "next_label": next_label,
            "error": bool(error),
        },
    )


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(
    request: Request,
    next: Optional[str] = None,
    remember: Optional[str] = None,
):
    bot_token = config.tg_login_bot_token
    if not bot_token:
        logger.error("TG_LOGIN_BOT_TOKEN not configured")
        return RedirectResponse("/login?error=1", status_code=307)

    params = dict(request.query_params)
    user = tg_auth.verify_login_widget(params, bot_token)
    if user is None:
        logger.warning("login widget verification failed: %s", params.get("hash", "?"))
        return RedirectResponse("/login?error=1", status_code=307)

    tg_id = user["id"]
    if tg_id not in config.allowed_ids:
        logger.warning("login attempt by non-allowed tg_id=%s", tg_id)
        return RedirectResponse("/login?error=1", status_code=307)

    secret = config.session_secret
    if not secret:
        logger.error("SESSION_SECRET not configured")
        return RedirectResponse("/login?error=1", status_code=307)

    do_remember = remember == "1"
    token, max_age = tg_auth.issue_session(tg_id, secret=secret, remember=do_remember)

    redirect_to = _POST_LOGIN_PATH
    if next and _is_safe_next(next):
        redirect_to = next

    response = RedirectResponse(redirect_to, status_code=303)
    tg_auth.set_session_cookie(response, token, max_age, config.cookie_domain)
    return response


@router.post("/logout")
async def logout(request: Request):
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        key=tg_auth.SESSION_COOKIE,
        domain=config.cookie_domain,
        path="/",
    )
    return response
