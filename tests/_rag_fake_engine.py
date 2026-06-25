"""tests/_rag_fake_engine.py — фейковый SQLAlchemy Engine для unit-тестов core/rag.py.

rag.py использует pgvector-специфичный SQL (CAST AS vector, оператор <=>, ON
CONFLICT, тип vector) — SQLite это НЕ исполнит. Поэтому мокаем границу БД
(get_engine): захватываем execute-вызовы (SQL + params) и отдаём заготовленные
строки. Так проверяем построение SQL, параметры и парсинг результата без живого
pgvector — реальное исполнение SQL покрыто смоуком (#6) и backfill-прогоном (#8).

Не collected как тест-модуль (нет префикса test_), только импортируется.
"""
from __future__ import annotations

from unittest.mock import MagicMock


class _FakeResult:
    def __init__(self, rows, scalar):
        self._rows, self._scalar = rows, scalar

    def mappings(self):
        m = MagicMock()
        m.all.return_value = self._rows
        return m

    def scalar(self):
        return self._scalar


class _FakeConn:
    def __init__(self, capture, rows, scalar):
        self._capture, self._rows, self._scalar = capture, rows, scalar

    def execute(self, stmt, params=None):
        self._capture.append((str(stmt), params))
        return _FakeResult(self._rows, self._scalar)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEngine:
    """Замена core.db.get_engine() в тестах rag.

    begin()/connect() → контекст-менеджер с тем же conn (захват execute).
    rows  — что вернёт .mappings().all() (для search_triplets);
    scalar — что вернёт .scalar() (для ensure_collection: True = таблица есть).
    .calls — список (sql_str, params) всех execute (для ассертов).
    """
    def __init__(self, rows=None, scalar=True):
        self.calls: list = []
        self._rows, self._scalar = rows or [], scalar

    def begin(self):
        return _FakeConn(self.calls, self._rows, self._scalar)

    def connect(self):
        return _FakeConn(self.calls, self._rows, self._scalar)


def boom_engine():
    """get_engine, который бросает — имитирует недоступную БД (graceful-путь)."""
    raise RuntimeError("db down (mock)")
