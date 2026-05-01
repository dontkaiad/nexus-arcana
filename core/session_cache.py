"""core/session_cache.py — простой K/V-кеш саммари сессий раскладов.

Хранится рядом с message_pages.db в корне проекта. Без TTL — инвалидация
ручная: при изменении вердикта триплета вызываем `cache_delete(key)`.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).parent.parent / "session_cache.db"
_INITED = False


def _conn() -> sqlite3.Connection:
    global _INITED
    c = sqlite3.connect(_DB_PATH)
    if not _INITED:
        c.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            "  k TEXT PRIMARY KEY, v TEXT NOT NULL, ts INTEGER DEFAULT (strftime('%s','now'))"
            ")"
        )
        c.commit()
        _INITED = True
    return c


def slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^\wЀ-ӿ]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "_"


def session_summary_key(session_name: str, client_id: Optional[str]) -> str:
    return f"session_summary_{slugify(session_name)}_{client_id or 'self'}"


def cache_get(key: str) -> Optional[str]:
    try:
        c = _conn()
        cur = c.execute("SELECT v FROM kv WHERE k = ?", (key,))
        row = cur.fetchone()
        c.close()
        return row[0] if row else None
    except Exception:
        return None


def cache_set(key: str, value: str) -> None:
    try:
        c = _conn()
        c.execute(
            "INSERT INTO kv(k,v,ts) VALUES(?,?,strftime('%s','now')) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts",
            (key, value),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def cache_delete(key: str) -> None:
    try:
        c = _conn()
        c.execute("DELETE FROM kv WHERE k = ?", (key,))
        c.commit()
        c.close()
    except Exception:
        pass


def cache_delete_prefix(prefix: str) -> None:
    try:
        c = _conn()
        c.execute("DELETE FROM kv WHERE k LIKE ?", (prefix + "%",))
        c.commit()
        c.close()
    except Exception:
        pass
