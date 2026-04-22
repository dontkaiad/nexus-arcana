"""miniapp/backend/cache.py — SQLite cache for daily ADHD tips."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_DB_PATH = os.path.join(_DB_DIR, "adhd_cache.db")

_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS adhd_tips (
    tg_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (tg_id, date)
);
"""


def _init_db() -> None:
    os.makedirs(_DB_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(_CREATE_SQL)
        con.commit()
    finally:
        con.close()


_init_db()


def get_tip(tg_id: int, date: str) -> Optional[str]:
    con = sqlite3.connect(_DB_PATH)
    try:
        row = con.execute(
            "SELECT text FROM adhd_tips WHERE tg_id = ? AND date = ?",
            (tg_id, date),
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def set_tip(tg_id: int, date: str, text: str) -> None:
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(
            "INSERT OR REPLACE INTO adhd_tips (tg_id, date, text, created_at) "
            "VALUES (?, ?, ?, ?)",
            (tg_id, date, text, int(time.time())),
        )
        con.commit()
    finally:
        con.close()
