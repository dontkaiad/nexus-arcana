"""tests/test_task_completions.py — журнал выполнений задач (task_completions, миграция d5b8f1c3a720).

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL). Вымышленные названия, строки помечены MARK.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
from nexus.repos.pg_tasks_repo import PgTasksRepo
from nexus.repos.tasks_tables import task_completions, tasks

REPO = Path(__file__).resolve().parent.parent
MARK = "test-task-completions"


def _status_id(code="Not started"):
    with get_engine().connect() as c:
        return c.execute(text("SELECT id FROM task_status WHERE code = :c"), {"c": code}).scalar()


def _mk_task(title="Покормить рыбок", **kw) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(tasks).values(title=title, status_id=_status_id(), user_id=MARK, **kw).returning(tasks.c.id)).scalar()


def _rows(task_id):
    with get_engine().connect() as c:
        return c.execute(select(task_completions.c.done_by, task_completions.c.source)
                         .where(task_completions.c.task_id == task_id).order_by(task_completions.c.id)).all()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(delete(tasks).where(tasks.c.user_id == MARK))     # журнал уходит каскадом


def _load_migration():
    path = next((REPO / "alembic" / "versions").glob("*_task_completions.py"))
    spec = importlib.util.spec_from_file_location("_mig_tc", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def test_migration_chain_and_columns():
    assert _load_migration().down_revision == "c4a7e2b95d13"
    insp = inspect(get_engine())
    cols = {c["name"]: c for c in insp.get_columns("task_completions")}
    assert set(cols) == {"id", "task_id", "done_at", "done_by", "source", "cycle_due"}      # cycle_due добавлен миграцией c1a7e3b9d204
    assert cols["task_id"]["nullable"] is False and cols["done_by"]["nullable"] is True
    assert "ix_task_completions_task_done" in {i["name"] for i in insp.get_indexes("task_completions")}


def test_migration_up_down_reversible_and_backfill():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    done = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)
    tid = _mk_task(completed_at=done)
    tid_open = _mk_task("Открытая задача")
    eng = get_engine()
    with eng.begin() as conn:                                       # откат и повторный накат в одной транзакции
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "task_completions" not in inspect(conn).get_table_names()
            mig.upgrade()
        rows = conn.execute(text("SELECT source, done_at FROM task_completions WHERE task_id = :t"), {"t": tid}).all()
        assert [(r.source, r.done_at) for r in rows] == [("backfill", done)]
        assert conn.execute(text("SELECT count(*) FROM task_completions WHERE task_id = :t"), {"t": tid_open}).scalar() == 0


def test_backfill_is_idempotent_and_skips_tasks_that_already_have_rows():
    mig = _load_migration()
    done = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)
    tid = _mk_task(completed_at=done)
    other = _mk_task("Другая", completed_at=done)
    with get_engine().begin() as c:
        c.execute(insert(task_completions).values(task_id=other, source="miniapp"))   # уже есть живая запись
        c.execute(text(mig.BACKFILL_SQL))
        c.execute(text(mig.BACKFILL_SQL))
    assert _rows(tid) == [(None, "backfill")]
    assert _rows(other) == [(None, "miniapp")]


@pytest.mark.asyncio
async def test_bot_recurring_done_records_with_author():
    import nexus.handlers.tasks as tm
    msg = AsyncMock()
    task = MagicMock(repeat_time="16:00|every_2d")
    with patch.object(tm._repo, "set_in_progress", AsyncMock()), \
         patch.object(tm._repo, "record_completion", AsyncMock(return_value=True)) as rec, \
         patch.object(tm, "_get_user_tz", AsyncMock(return_value=3)), \
         patch("nexus.handlers.streaks.update_streak", AsyncMock()), \
         patch("core.task_streaks.update_task_streak", MagicMock()), \
         patch.object(tm, "_handle_recurring_task_reset", AsyncMock()):
        await tm._handle_recurring_deadline_done(msg, "5", task, "Ежедневно", "Покормить рыбок", 7000001)
    rec.assert_awaited_once_with("5", done_by="7000001", source="bot")


@pytest.mark.asyncio
async def test_record_completion_writes_row():
    tid = _mk_task()
    assert await PgTasksRepo().record_completion(str(tid), done_by="7000001", source="miniapp") is True
    assert _rows(tid) == [("7000001", "miniapp")]


@pytest.mark.asyncio
async def test_record_completion_empty_done_by_is_null_and_repeatable():
    tid = _mk_task()
    r = PgTasksRepo()
    await r.record_completion(str(tid))
    await r.record_completion(str(tid))                              # несколько выполнений = несколько строк
    assert _rows(tid) == [(None, "app"), (None, "app")]


@pytest.mark.asyncio
async def test_set_status_done_records_once_other_statuses_do_not():
    tid = _mk_task()
    r = PgTasksRepo()
    with patch("core.booking.linkage.complete_linked_booking", AsyncMock()):
        await r.set_status(str(tid), "In progress")
        assert _rows(tid) == []
        await r.set_status(str(tid), "Done")
    assert _rows(tid) == [(None, "status")]


@pytest.mark.asyncio
async def test_failed_status_update_does_not_record():
    tid = _mk_task()
    with patch("nexus.repos.pg_tasks_repo._set_status_sync", return_value=False):
        await PgTasksRepo().set_status(str(tid), "Done")
    assert _rows(tid) == []


@pytest.mark.asyncio
async def test_record_failure_never_raises():
    assert await PgTasksRepo().record_completion("999999999999", source="app") is False   # нет такой задачи (FK)
    assert await PgTasksRepo().record_completion("не-число") is False


def test_fk_cascade_on_task_delete():
    tid = _mk_task()
    with get_engine().begin() as c:
        c.execute(insert(task_completions).values(task_id=tid))
    with get_engine().begin() as c:
        c.execute(delete(tasks).where(tasks.c.id == tid))
    assert _rows(tid) == []


def test_fk_rejects_unknown_task():
    with pytest.raises(IntegrityError):
        with get_engine().begin() as c:
            c.execute(insert(task_completions).values(task_id=999999999999))


# ── Mini App: «done» ─────────────────────────────────────────────────────────

async def _call_task_done(repeat_time: str, tid: int):
    import miniapp.backend.routes.writes as w
    task = MagicMock(title="Покормить рыбок", repeat="Ежедневно" if repeat_time else "Нет", repeat_time=repeat_time)
    with patch.object(w, "get_user_id", AsyncMock(return_value=MARK)), \
         patch.object(w, "_load_owned_task", AsyncMock(return_value=task)), \
         patch.object(w, "today_user_tz", AsyncMock(return_value=(date(2026, 11, 5), 3))), \
         patch.object(w, "notify_user", AsyncMock()), patch.object(w, "clear_task_reminder", AsyncMock()), \
         patch.object(w, "_cancel_task_jobs", MagicMock()), \
         patch("core.task_streaks.update_task_streak", MagicMock()), \
         patch("nexus.handlers.streaks.update_streak", AsyncMock()), \
         patch("core.booking.linkage.complete_linked_booking", AsyncMock()):
        return await w.task_done(str(tid), tg_id=7000001)


@pytest.mark.asyncio
async def test_miniapp_done_repeating_records_one_row_with_author():
    tid = _mk_task(repeat_time="16:00|every_2d")
    res = await _call_task_done("16:00|every_2d", tid)
    assert res["status"] == "In progress"
    assert _rows(tid) == [("7000001", "miniapp")]


@pytest.mark.asyncio
async def test_miniapp_done_oneoff_records_exactly_one_row():
    tid = _mk_task()
    res = await _call_task_done("", tid)
    assert res["status"] == "Done"
    assert _rows(tid) == [(None, "status")]                          # не два: журнал пишет set_status, явный вызов пропущен


def test_facade_exposes_record_completion_like_pg_repo():
    """Бот ходит через фасад TasksRepo, Mini App — через PgTasksRepo: метод должен быть у обоих (иначе вызов тихо падает в except)."""
    import inspect
    from nexus.repos.tasks_repo import TasksRepo
    assert inspect.signature(TasksRepo.record_completion) == inspect.signature(PgTasksRepo.record_completion)


@pytest.mark.asyncio
async def test_facade_writes_through_to_journal():
    from nexus.repos.tasks_repo import TasksRepo
    tid = _mk_task()
    assert await TasksRepo().record_completion(str(tid), done_by="7000001", source="bot") is True
    assert _rows(tid) == [("7000001", "bot")]
