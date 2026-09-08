"""tests/test_pending_kv.py — универсальный persist-pending KV."""
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def kv(tmp_path):
    import core.pending_kv as m
    with patch.object(m, "_DB_PATH", str(tmp_path / "pk.db")):
        yield m


def test_save_get_pop_roundtrip(kv):
    kv.save(7, "fin", {"a": 1, "txt": "кофе"})
    assert kv.get(7, "fin") == {"a": 1, "txt": "кофе"}
    assert kv.has(7, "fin") is True
    assert kv.pop(7, "fin") == {"a": 1, "txt": "кофе"}
    assert kv.get(7, "fin") is None
    assert kv.has(7, "fin") is False


def test_missing_returns_none(kv):
    assert kv.get(1, "nope") is None
    assert kv.pop(1, "nope") is None


def test_overwrite(kv):
    kv.save(7, "fin", {"v": 1})
    kv.save(7, "fin", {"v": 2})
    assert kv.get(7, "fin") == {"v": 2}


def test_scoped_by_uid_and_kind(kv):
    kv.save(1, "a", {"x": "one"})
    kv.save(2, "a", {"x": "two"})
    kv.save(1, "b", {"x": "three"})
    assert kv.get(1, "a") == {"x": "one"}
    assert kv.get(2, "a") == {"x": "two"}
    assert kv.get(1, "b") == {"x": "three"}
    kv.delete(1, "a")
    assert kv.get(1, "a") is None
    assert kv.get(2, "a") == {"x": "two"}   # не задели


def test_ttl_expiry(kv):
    kv.save(7, "fin", {"v": 1})
    assert kv.get(7, "fin", ttl=3600) == {"v": 1}
    # искусственно состариваем строку
    import sqlite3
    with sqlite3.connect(kv._DB_PATH) as con:
        con.execute("UPDATE pending_kv SET ts = ? WHERE uid=7", (time.time() - 10_000,))
    assert kv.get(7, "fin", ttl=3600) is None   # протухло + удалено
    with sqlite3.connect(kv._DB_PATH) as con:
        n = con.execute("SELECT COUNT(*) FROM pending_kv WHERE uid=7").fetchone()[0]
    assert n == 0


def test_survives_reconnect(kv):
    """Данные переживают «рестарт» — новое соединение видит ту же строку."""
    kv.save(7, "fin", {"v": 42})
    # каждый вызов открывает новое соединение — эмуляция рестарта процесса
    assert kv.get(7, "fin") == {"v": 42}
