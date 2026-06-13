"""core/db.py — SQLAlchemy Engine singleton.

Usage:
    from core.db import get_engine
    with get_engine().connect() as conn:
        ...
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, Engine

load_dotenv()

logger = logging.getLogger("core.db")

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL not set — check .env")
        _engine = create_engine(url, pool_pre_ping=True)
        logger.info("SQLAlchemy engine created: %s", url.split("@")[-1])
    return _engine
