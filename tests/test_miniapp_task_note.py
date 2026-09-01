"""tests/test_miniapp_task_note.py — Mini App отдаёт заметку задачи.

Заметка (tasks.note) уже была видна в Telegram (карточка /tasks, «Задача
создана»), но _serialize_pg_task её не сериализовал. Фронт проверяет на
falsy, поэтому пустая заметка → None, а не "".
"""
from __future__ import annotations

from datetime import date

from miniapp.backend.routes.tasks import _serialize_pg_task
from nexus.repos.pg_tasks_repo import Task as PgTask


def _task(**kw):
    base = dict(id="t1", title="прийти к нотариусу", status="Not started",
                priority="🔴 Срочно", category="💳 Прочее")
    base.update(kw)
    return PgTask(**base)


def test_serialize_includes_note():
    out = _serialize_pg_task(_task(note="оплата 1250"), date(2026, 9, 2), 3)
    assert out["note"] == "оплата 1250"


def test_serialize_note_empty_is_none():
    for val in ("", None):
        out = _serialize_pg_task(_task(note=val), date(2026, 9, 2), 3)
        assert out["note"] is None
