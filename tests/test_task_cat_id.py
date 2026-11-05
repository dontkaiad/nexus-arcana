"""tests/test_task_cat_id.py — привязка задачи к коту (tasks.cat_id, миграция e6c9a2d4b831).

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL). Вымышленные имена (Тучка, Пончик), строки помечены MARK/MARK2.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, insert, inspect, select, text

from core.db import get_engine
from core.repos.cats_tables import cats
from nexus.repos.pg_tasks_repo import PgTasksRepo
from nexus.repos.tasks_tables import tasks

REPO = Path(__file__).resolve().parent.parent
MARK, MARK2 = "test-task-cat", "test-task-cat-other"


def _status_id():
    with get_engine().connect() as c:
        return c.execute(text("SELECT id FROM task_status WHERE code = 'Not started'")).scalar()


def _mk_task(user=MARK, title="Менять лотки") -> int:
    with get_engine().begin() as c:
        return c.execute(insert(tasks).values(title=title, status_id=_status_id(), user_id=user).returning(tasks.c.id)).scalar()


def _mk_cat(user=MARK, name="Тучка", status="confirmed") -> int:
    with get_engine().begin() as c:
        return c.execute(insert(cats).values(user_id=user, name=name, status=status).returning(cats.c.id)).scalar()


def _cat_of(tid):
    with get_engine().connect() as c:
        return c.execute(select(tasks.c.cat_id).where(tasks.c.id == tid)).scalar()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(delete(tasks).where(tasks.c.user_id.in_([MARK, MARK2])))
        c.execute(delete(cats).where(cats.c.user_id.in_([MARK, MARK2])))


def _load_migration():
    path = next((REPO / "alembic" / "versions").glob("*_tasks_cat_id.py"))
    spec = importlib.util.spec_from_file_location("_mig_tcat", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def test_migration_chain_column_and_partial_index():
    assert _load_migration().down_revision == "d5b8f1c3a720"
    insp = inspect(get_engine())
    cols = {c["name"]: c for c in insp.get_columns("tasks")}
    assert "cat_id" in cols and cols["cat_id"]["nullable"] is True
    idx = {i["name"]: i for i in insp.get_indexes("tasks")}
    assert "ix_tasks_cat" in idx
    with get_engine().connect() as c:
        d = c.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_tasks_cat'")).scalar()
    assert "WHERE (cat_id IS NOT NULL)" in d


def test_migration_up_down_reversible_keeps_tasks():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    cid = _mk_cat()
    tid = _mk_task()
    with get_engine().begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "cat_id" not in {c["name"] for c in inspect(conn).get_columns("tasks")}
            assert conn.execute(text("SELECT count(*) FROM tasks WHERE id = :t"), {"t": tid}).scalar() == 1   # задача цела
            mig.upgrade()
        assert conn.execute(text("SELECT cat_id FROM tasks WHERE id = :t"), {"t": tid}).scalar() is None
    assert cid


@pytest.mark.asyncio
async def test_set_cat_attaches_and_detaches():
    cid, tid = _mk_cat(), _mk_task()
    repo = PgTasksRepo()
    assert await repo.set_cat(str(tid), cid) is True
    assert _cat_of(tid) == cid
    assert (await repo.retrieve_page(str(tid))).cat_id == cid
    assert await repo.set_cat(str(tid), None) is True
    assert _cat_of(tid) is None
    assert (await repo.retrieve_page(str(tid))).cat_id is None


@pytest.mark.asyncio
async def test_set_cat_unknown_task_or_cat_returns_false():
    cid, tid = _mk_cat(), _mk_task()
    repo = PgTasksRepo()
    assert await repo.set_cat("999999999999", cid) is False        # нет задачи
    assert await repo.set_cat(str(tid), 999999999999) is False     # нет кота (FK)
    assert _cat_of(tid) is None


def test_deleting_cat_sets_task_cat_null_not_deleting_task():
    cid, tid = _mk_cat(), _mk_task()
    with get_engine().begin() as c:
        c.execute(tasks.update().where(tasks.c.id == tid).values(cat_id=cid))
    with get_engine().begin() as c:
        c.execute(delete(cats).where(cats.c.id == cid))
    assert _cat_of(tid) is None
    with get_engine().connect() as c:
        assert c.execute(select(tasks.c.id).where(tasks.c.id == tid)).first() is not None


def test_task_payload_exposes_cat_id():
    from miniapp.backend.routes.tasks import _serialize_pg_task
    from nexus.repos.pg_tasks_repo import Task
    out = _serialize_pg_task(Task(id="1", title="Менять лотки", cat_id=5), date(2026, 11, 5), 3)
    assert out["cat_id"] == 5
    assert _serialize_pg_task(Task(id="1", title="x"), date(2026, 11, 5), 3)["cat_id"] is None


# ── Mini App: POST /tasks/{id}/cat ───────────────────────────────────────────

async def _post(tid, cat_id, user=MARK):
    import miniapp.backend.routes.writes as w
    with patch.object(w, "get_user_id", AsyncMock(return_value=user)):
        return await w.task_set_cat(str(tid), w.TaskCatBody(cat_id=cat_id), tg_id=7000001)


@pytest.mark.asyncio
async def test_route_attach_and_detach():
    cid, tid = _mk_cat(), _mk_task()
    assert await _post(tid, cid) == {"ok": True, "cat_id": cid}
    assert _cat_of(tid) == cid
    assert await _post(tid, None) == {"ok": True, "cat_id": None}
    assert _cat_of(tid) is None


@pytest.mark.asyncio
async def test_route_rejects_foreign_cat():
    foreign, tid = _mk_cat(user=MARK2, name="Пончик"), _mk_task()
    with pytest.raises(HTTPException) as e:
        await _post(tid, foreign)
    assert e.value.status_code == 404 and _cat_of(tid) is None


@pytest.mark.asyncio
async def test_route_rejects_rejected_and_missing_cat():
    rejected, tid = _mk_cat(name="Рыжик", status="rejected"), _mk_task()
    for bad in (rejected, 999999999999):
        with pytest.raises(HTTPException) as e:
            await _post(tid, bad)
        assert e.value.status_code == 404
    assert _cat_of(tid) is None


@pytest.mark.asyncio
async def test_route_foreign_task_is_404_and_untouched():
    cid, foreign_task = _mk_cat(), _mk_task(user=MARK2)
    with pytest.raises(HTTPException) as e:
        await _post(foreign_task, cid)
    assert e.value.status_code == 404 and _cat_of(foreign_task) is None


@pytest.mark.asyncio
async def test_route_unknown_task_is_404():
    cid = _mk_cat()
    with pytest.raises(HTTPException) as e:
        await _post("999999999999", cid)
    assert e.value.status_code == 404


def test_facade_has_set_cat():
    import inspect as _i
    from nexus.repos.tasks_repo import TasksRepo
    assert _i.signature(TasksRepo.set_cat) == _i.signature(PgTasksRepo.set_cat)


@pytest.mark.asyncio
async def test_facade_writes_through():
    from nexus.repos.tasks_repo import TasksRepo
    cid, tid = _mk_cat(), _mk_task()
    assert await TasksRepo().set_cat(str(tid), cid) is True
    assert _cat_of(tid) == cid
