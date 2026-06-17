"""tests/test_find_by_exact_key.py — регрессия #126.

find_by_exact_key("tz_123") НЕ возвращает запись key="note_x" у которой "tz_123" в fact_text
(точный матч key_name==, не ilike по тексту факта).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool


def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "notion_id TEXT UNIQUE, "
            "fact_text TEXT NOT NULL DEFAULT '', "
            "key_name TEXT NOT NULL DEFAULT '', "
            "value_text TEXT NOT NULL DEFAULT '', "
            "category TEXT NOT NULL DEFAULT '', "
            "scope TEXT NOT NULL DEFAULT 'global', "
            "source TEXT NOT NULL DEFAULT 'manual', "
            "related_to TEXT NOT NULL DEFAULT '', "
            "is_current INTEGER NOT NULL DEFAULT 1, "
            "is_archived INTEGER NOT NULL DEFAULT 0, "
            "user_notion_id TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        # Ложное совпадение: key_name != "tz_123", но fact_text содержит "tz_123"
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, fact_text, is_current) "
            "VALUES ('note_x', 'contact tz_123 info', 1)"
        ))
        # Правильная запись
        conn.execute(sa.text(
            "INSERT INTO memories (key_name, fact_text, is_current) "
            "VALUES ('tz_123', '3', 1)"
        ))
    return eng


@pytest.mark.asyncio
async def test_find_by_exact_key_no_fact_text_leak():
    """find_by_exact_key('tz_123') возвращает только key_name=='tz_123', не 'note_x'."""
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with patch.object(pgmod, "get_engine", return_value=eng):
        repo = PgMemoryRepo()
        results = await repo.find_by_exact_key("tz_123")

    assert len(results) == 1, "должна вернуться ровно одна запись"
    assert results[0].key == "tz_123"
    assert results[0].fact == "3"


@pytest.mark.asyncio
async def test_find_by_exact_key_nonexistent_returns_empty():
    """find_by_exact_key с несуществующим ключом → пустой список."""
    import core.repos.pg_memory_repo as pgmod
    from core.repos.pg_memory_repo import PgMemoryRepo

    eng = _make_engine()
    with patch.object(pgmod, "get_engine", return_value=eng):
        repo = PgMemoryRepo()
        results = await repo.find_by_exact_key("tz_999")

    assert results == []
