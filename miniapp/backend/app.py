"""miniapp/backend/app.py — FastAPI app for Nexus × Arcana mini app."""
from __future__ import annotations

import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import config
from miniapp.backend import tg_auth
from miniapp.backend.routes import today, tasks, finance, lists, memory, writes
from miniapp.backend.routes import calendar as cal
from miniapp.backend.routes import categories
from miniapp.backend.routes import streaks
from miniapp.backend.routes import weather
from miniapp.backend.routes import login as login_routes
from miniapp.backend.routes import (
    arcana_today,
    arcana_sessions,
    arcana_clients,
    arcana_rituals,
    arcana_grimoire,
    arcana_inventory,
    arcana_finance,
    arcana_barter,
    arcana_debts,
)

app = FastAPI(title="Nexus × Arcana API")

# CORS: дефолт = telegram WebApp + локальный vite dev. Доп. домены — через
# env MINIAPP_CORS_ORIGINS (CSV, перекрывает дефолт). Эфемерные tunnel-URL
# Cloudflare разработки разрешены через regex.
_DEFAULT_ORIGINS = [
    "https://web.telegram.org",
    "https://webk.telegram.org",
    "https://webz.telegram.org",
    "https://t.me",
    "http://localhost:5173",
    "http://localhost:5174",
]
_origins_env = os.getenv("MINIAPP_CORS_ORIGINS", "").strip()
allowed_origins = (
    [o.strip() for o in _origins_env.split(",") if o.strip()]
    if _origins_env
    else _DEFAULT_ORIGINS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^https://.*\.trycloudflare\.com$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Пути, открытые без hl_session: сам логин-редирект, логаут, health-check
# (его дёргает docker healthcheck без cookie) и /api/* (там своя проверка
# через current_user_id — initData из Telegram WebApp тоже валиден).
_PUBLIC_PREFIXES = ("/login", "/logout", "/health", "/api")


@app.middleware("http")
async def require_owner_session(request: Request, call_next):
    """Гейт для страничных (не /api) запросов: единый вход + только владелец.

    /api/* сюда не попадает — там auth.current_user_id уже проверяет и
    cookie, и Telegram WebApp initData. Здесь закрываем оставшуюся дыру:
    браузер, зашедший на core.heylark.dev напрямую (не из Mini App), должен
    залогиниться через login.heylark.dev, иначе видит SPA без данных.
    """
    path = request.url.path
    if path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)

    session_cookie = request.cookies.get(tg_auth.SESSION_COOKIE)
    tg_id = tg_auth.read_session(session_cookie, secret=config.session_secret) if session_cookie else None

    if tg_id is None:
        next_url = f"{path}?{request.url.query}" if request.url.query else path
        return RedirectResponse(f"/login?next={next_url}", status_code=307)
    if tg_id not in config.allowed_ids:
        return PlainTextResponse("Доступ запрещён.", status_code=403)

    return await call_next(request)


app.include_router(login_routes.router)  # /login, /logout — no /api prefix

for _r in (
    today, tasks, finance, lists, memory, cal, categories, streaks, weather,
    arcana_today, arcana_sessions, arcana_clients,
    arcana_rituals, arcana_grimoire,
    arcana_inventory, arcana_finance, arcana_barter, arcana_debts,
    writes,
):
    app.include_router(_r.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


class SPAStaticFiles(StaticFiles):
    """StaticFiles с SPA-fallback: неизвестный путь фронта → index.html (200).

    Пути /api/* НЕ получают fallback — их 404 пробрасывается как есть,
    чтобы JS-клиент мог распознать ошибку API.
    Starlette strip-ает mount-prefix ("/"), поэтому path здесь без ведущего "/":
    запрос /nexus → path="nexus", запрос /api/foo → path="api/foo".
    """
    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api/"):
                response = await super().get_response("index.html", scope)
            else:
                raise
        # index.html не кэшируем: Telegram WebApp иначе залипает на старом
        # бандле (ссылка на хэшированный JS живёт в index.html). Сам JS/CSS —
        # immutable по хэшу в имени, их StaticFiles кэширует как обычно.
        if (getattr(response, "media_type", "") or "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# Статика: монтируем ПОСЛЕ /api и /health, чтобы роутеры выигрывали.
# В dev без собранного dist — mount пропускается, бэкенд работает как API.
_DIST = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(_DIST), html=True), name="spa")
