"""Regression: `PgTasksRepo.create()`/`_create_sync` never accepted a
duration at all — even after `nexus/handlers/tasks.py` started building
`props["Длительность"]` at task-creation time, the repo silently dropped it
(only the separate post-creation edit path, `_set_props_sync`'s
`field == "Длительность"` branch, ever wrote `duration_min`). Confirmed via
a real prod task ("встреча с Анубис", "длительность 2 часа") whose
`duration_min` stayed NULL after creation.

Privacy: synthetic title.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import nexus.repos.pg_tasks_repo as R
from core.props import _number, _title, _select, _date


def _fake_engine_capturing(captured: dict):
    """Minimal get_engine() stand-in: records values() kwargs from insert()."""
    conn = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = (42,)
    conn.execute.return_value = result

    class _Insert:
        def values(self, **kwargs):
            captured.update(kwargs)
            return self

        def returning(self, *a, **k):
            return self

    orig_insert = R.tasks.insert

    def _insert():
        return _Insert()

    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    engine.begin.return_value.__exit__.return_value = False
    return engine, orig_insert, _insert


@pytest.mark.asyncio
async def test_create_persists_duration_from_props():
    captured: dict = {}
    engine, orig_insert, fake_insert = _fake_engine_capturing(captured)

    with patch.object(R, "get_engine", return_value=engine), \
         patch.object(R.tasks, "insert", fake_insert), \
         patch.object(R, "_ensure_lookups", lambda: None), \
         patch.object(R, "_status_id", {"Not started": 1}), \
         patch.object(R, "_priority_id", {"🟡 Важно": 1}), \
         patch.object(R, "_category_id", {"💳 Прочее": 1}):
        repo = R.PgTasksRepo()
        props = {
            "Задача": _title("встреча с Анубис"),
            "Статус": {"status": {"name": "Not started"}},
            "Приоритет": _select("🟡 Важно"),
            "Категория": _select("💳 Прочее"),
            "Дедлайн": _date("2026-09-15T17:30+03:00"),
            "Длительность": _number(120),
        }
        result = await repo.create("db1", props)

    assert result == "42"
    assert captured.get("duration_min") == 120


@pytest.mark.asyncio
async def test_create_without_duration_prop_stores_none():
    captured: dict = {}
    engine, orig_insert, fake_insert = _fake_engine_capturing(captured)

    with patch.object(R, "get_engine", return_value=engine), \
         patch.object(R.tasks, "insert", fake_insert), \
         patch.object(R, "_ensure_lookups", lambda: None), \
         patch.object(R, "_status_id", {"Not started": 1}), \
         patch.object(R, "_priority_id", {"🟡 Важно": 1}), \
         patch.object(R, "_category_id", {"💳 Прочее": 1}):
        repo = R.PgTasksRepo()
        props = {
            "Задача": _title("купить корм"),
            "Статус": {"status": {"name": "Not started"}},
            "Приоритет": _select("🟡 Важно"),
            "Категория": _select("💳 Прочее"),
        }
        await repo.create("db1", props)

    assert captured.get("duration_min") is None
