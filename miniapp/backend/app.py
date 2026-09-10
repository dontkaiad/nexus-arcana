"""miniapp/backend/app.py — FastAPI app for Nexus × Arcana mini app."""
from __future__ import annotations

import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

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

# NB: there is deliberately no page-level "require hl_session cookie or
# redirect to /login" gate here. A real Telegram Mini App load of /nexus or
# /arcana NEVER carries the hl_session cookie — Telegram only ever hands the
# app initData (via the WebApp JS bridge, sent as a header on /api/* calls),
# not a cookie, on the top-level HTML request. A cookie-gate on these paths
# was tried once (see git history) and it 307-redirected every real Mini App
# session straight to login.heylark.dev before any of its own JS ever ran —
# the login widget shown there doesn't even work inside Telegram's own
# WebView (nested iframe), so that redirect was a dead end for every user.
# The actual auth boundary is /api/* (auth.current_user_id, cookie OR
# initData) — the SPA shell itself is just static JS/HTML with no data in
# it, so serving it unauthenticated leaks nothing.
app.include_router(login_routes.router)  # /login, /logout — no /api prefix

from miniapp.backend.routes import booking as booking_routes  # noqa: E402
app.include_router(booking_routes.feed_router)  # /feed/<token>.ics — no /api prefix (#23)

for _r in (
    today, tasks, finance, lists, memory, cal, categories, streaks, weather,
    arcana_today, arcana_sessions, arcana_clients,
    arcana_rituals, arcana_grimoire,
    arcana_inventory, arcana_finance, arcana_barter, arcana_debts,
    writes, booking_routes,
):
    app.include_router(_r.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


# booking.heylark.dev пока указывает на этот же бэкенд (API + .ics живут, фронт —
# B5). До фронта отдаём заглушку Zarya, а не оболочку Mini App. #23.
_ZARYA_PLACEHOLDER = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Zarya · heylark Booking</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    background:radial-gradient(1200px 600px at 50% -10%,#1a2340,#0b1020);color:#d8d4c8}
  .c{max-width:32rem;padding:2rem;text-align:center}
  h1{font-size:2rem;margin:.2rem 0}
  p{color:#8b93a7}
  code{background:#ffffff14;padding:.15em .4em;border-radius:6px;font-size:.85em}
</style>
<div class=c>
  <div style="font-size:3rem">⭐</div>
  <h1>Zarya</h1>
  <p>heylark Booking — календарь и запись. Страница в разработке.</p>
  <p style="font-size:.85em">API уже живёт: <code>/api/booking/*</code> · <code>/feed/&lt;token&gt;.ics</code></p>
</div>
"""


@app.get("/", include_in_schema=False)
async def root(request: Request):
    """`/` host-aware: booking.* → заглушка Zarya; остальное → оболочка Mini App
    (то же поведение, что раньше давал SPA-fallback для корня)."""
    if (request.headers.get("host", "") or "").split(":")[0].startswith("booking."):
        return HTMLResponse(_ZARYA_PLACEHOLDER)
    _idx = _DIST / "index.html"
    if _idx.is_file():
        return FileResponse(str(_idx), headers={"Cache-Control": "no-cache, must-revalidate"})
    return JSONResponse({"ok": True, "app": "nexus-arcana"})


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
