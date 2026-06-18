"""miniapp/backend/app.py — FastAPI app for Nexus × Arcana mini app."""
from __future__ import annotations

import os
import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from miniapp.backend.routes import today, tasks, finance, lists, memory, writes
from miniapp.backend.routes import calendar as cal
from miniapp.backend.routes import categories
from miniapp.backend.routes import streaks
from miniapp.backend.routes import weather
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


# Статика: монтируем ПОСЛЕ /api и /health, чтобы роутеры выигрывали.
# Навигация в Mini App — чистый React state, не URL-роутер, поэтому
# html=True (отдать index.html для несуществующих путей) достаточно.
# В dev без собранного dist — mount пропускается, бэкенд работает как API.
_DIST = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
