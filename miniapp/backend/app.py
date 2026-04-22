"""miniapp/backend/app.py — FastAPI app for Nexus × Arcana mini app."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from miniapp.backend.routes import today, tasks, finance, lists, memory
from miniapp.backend.routes import calendar as cal

app = FastAPI(title="Nexus × Arcana API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: сузить до домена mini app
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(today.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(finance.router, prefix="/api")
app.include_router(lists.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(cal.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
