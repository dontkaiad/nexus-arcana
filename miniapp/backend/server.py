"""miniapp/backend/server.py — uvicorn runner для встраивания в процесс Nexus.

Порт 8000. Проверить занятость: `lsof -i :8000`.
"""
from __future__ import annotations

import logging

import uvicorn

from miniapp.backend.app import app

logger = logging.getLogger("miniapp.server")


async def run_api_in_background(host: str = "0.0.0.0", port: int = 8000) -> None:
    config = uvicorn.Config(app, host=host, port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    logger.info("Starting FastAPI on %s:%s", host, port)
    await server.serve()
