"""core/pending_kv.py — универсальный persist-pending KV поверх SQLite.

Заменяет in-memory dict'ы «жду ответ / нажатие кнопки», которые молча
теряются при рестарте процесса (частый auto-reload на деплое = потерянный
диалог у пользователя). Одна таблица, ключ `(uid, kind)`, JSON-значение,
per-call TTL, авто-init + ленивый свип протухших строк.

Sync (как `arcana/handlers/work_preview.py`): операции — крошечные точечные
SELECT/INSERT, блокировка loop пренебрежимо мала.

Пример:
    from core import pending_kv
    pending_kv.save(uid, "fin_confirm", {"data": parsed, "user_id": uid}, ttl=3600)
    st = pending_kv.pop(uid, "fin_confirm")   # dict | None
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("core.pending_kv")

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pending_kv.db")
_DEFAULT_TTL = 1800  # 30 минут


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS pending_kv ("
        "uid INTEGER NOT NULL, kind TEXT NOT NULL, "
        "value TEXT NOT NULL, ts REAL NOT NULL, "
        "PRIMARY KEY (uid, kind))"
    )
    con.commit()
    return con


def _sweep(con: sqlite3.Connection) -> None:
    # Протухшие строки удаляем разом: TTL хранится не в строке, а задаётся
    # при чтении — поэтому свипаем по грубому потолку (сутки), чисто чтобы
    # таблица не пухла от брошенных диалогов.
    try:
        con.execute("DELETE FROM pending_kv WHERE ts < ?", (time.time() - 86400,))
    except Exception:
        pass


def save(uid: int, kind: str, value: Dict[str, Any], ttl: int = _DEFAULT_TTL) -> None:
    """Сохранить/перезаписать pending-состояние для (uid, kind)."""
    try:
        with _con() as con:
            con.execute(
                "INSERT OR REPLACE INTO pending_kv (uid, kind, value, ts) VALUES (?,?,?,?)",
                (int(uid), kind, json.dumps(value, ensure_ascii=False), time.time()),
            )
            _sweep(con)
    except Exception as e:
        logger.warning("pending_kv.save(%s,%s) failed: %s", uid, kind, e)


def get(uid: int, kind: str, ttl: int = _DEFAULT_TTL) -> Optional[Dict[str, Any]]:
    """Прочитать pending-состояние; None если нет или протухло (протухшее
    удаляется)."""
    try:
        with _con() as con:
            row = con.execute(
                "SELECT value, ts FROM pending_kv WHERE uid=? AND kind=?",
                (int(uid), kind),
            ).fetchone()
        if not row:
            return None
        if time.time() - row[1] > ttl:
            delete(uid, kind)
            return None
        return json.loads(row[0])
    except Exception as e:
        logger.warning("pending_kv.get(%s,%s) failed: %s", uid, kind, e)
        return None


def pop(uid: int, kind: str, ttl: int = _DEFAULT_TTL) -> Optional[Dict[str, Any]]:
    """get() + delete() одной операцией."""
    val = get(uid, kind, ttl)
    if val is not None:
        delete(uid, kind)
    return val


def delete(uid: int, kind: str) -> None:
    try:
        with _con() as con:
            con.execute(
                "DELETE FROM pending_kv WHERE uid=? AND kind=?", (int(uid), kind)
            )
    except Exception as e:
        logger.warning("pending_kv.delete(%s,%s) failed: %s", uid, kind, e)


def has(uid: int, kind: str, ttl: int = _DEFAULT_TTL) -> bool:
    return get(uid, kind, ttl) is not None
