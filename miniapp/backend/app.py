"""miniapp/backend/app.py — FastAPI app for Nexus × Arcana mini app."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from miniapp.backend.routes import today, tasks, finance, lists, memory, writes
from miniapp.backend.routes import calendar as cal
from miniapp.backend.routes import categories
from miniapp.backend.routes import (
    arcana_today,
    arcana_sessions,
    arcana_clients,
    arcana_rituals,
    arcana_grimoire,
    arcana_stats,
)

app = FastAPI(title="Nexus × Arcana API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: сузить до домена mini app
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

for _r in (
    today, tasks, finance, lists, memory, cal, categories,
    arcana_today, arcana_sessions, arcana_clients,
    arcana_rituals, arcana_grimoire, arcana_stats,
    writes,
):
    app.include_router(_r.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
