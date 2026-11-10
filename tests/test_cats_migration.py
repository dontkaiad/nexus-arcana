"""tests/test_cats_migration.py — Cats subject, step 2: миграция cats_subject (`cats` + `memories.cat_id`).

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL). Фикстуры — вымышленные имена, строки помечены MARK.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
from core.repos.cats_tables import cats
from core.repos.memories_table import memories

REPO = Path(__file__).resolve().parent.parent
MARK = "test-cats-step2"


def _load_migration():
    path = next((REPO / "alembic" / "versions").glob("*_cats_subject.py"))
    spec = importlib.util.spec_from_file_location("_mig_cats", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def _load_health_migration():
    path = next((REPO / "alembic" / "versions").glob("*_cat_health.py"))
    spec = importlib.util.spec_from_file_location("_mig_cat_health_dep", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def _load_dependent_migration():
    path = next((REPO / "alembic" / "versions").glob("*_tasks_cat_id.py"))
    spec = importlib.util.spec_from_file_location("_mig_tasks_cat", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id == MARK))
        conn.execute(delete(cats).where(cats.c.user_id == MARK))


def test_migration_chain_and_columns():
    mig = _load_migration()
    assert mig.down_revision == "b3e7c9a14d58"
    insp = inspect(get_engine())
    assert "cats" in insp.get_table_names()
    cols = {c["name"]: c for c in insp.get_columns("cats")}
    assert set(cols) == {"id", "user_id", "name", "aliases", "aliases_norm", "photo_url",
                         "status", "created_at", "updated_at"}
    assert cols["photo_url"]["nullable"] is True
    mem_cols = {c["name"]: c for c in insp.get_columns("memories")}
    assert "cat_id" in mem_cols and mem_cols["cat_id"]["nullable"] is True
    idx = {i["name"] for i in insp.get_indexes("memories")}
    assert "ix_memories_cat" in idx
    assert "ix_cats_aliases_norm" in {i["name"] for i in insp.get_indexes("cats")}


def test_migration_up_down_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    eng = get_engine()
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                # tasks.cat_id (FK на cats) появился позже — откатываем зависимое первым, как это делает alembic.
                dep = _load_dependent_migration()
                health = _load_health_migration()          # таблицы здоровья ссылаются на cats: откатываются раньше неё
                health.downgrade()
                dep.downgrade()
                mig.downgrade()
                insp = inspect(conn)
                assert "cats" not in insp.get_table_names()
                assert "cat_id" not in [c["name"] for c in insp.get_columns("memories")]
                assert "ix_memories_cat" not in {i["name"] for i in insp.get_indexes("memories")}
                mig.upgrade()
                dep.upgrade()
                health.upgrade()
                insp = inspect(conn)
                assert "cats" in insp.get_table_names()
                assert "cat_id" in [c["name"] for c in insp.get_columns("memories")]
                assert "cat_id" in [c["name"] for c in insp.get_columns("tasks")]
        finally:
            trans.rollback()


def test_existing_memory_rows_untouched_by_cat_id():
    with get_engine().begin() as conn:
        mid = conn.execute(insert(memories).values(fact_text="факт без кота", user_id=MARK)
                           .returning(memories.c.id)).scalar_one()
        assert conn.execute(select(memories.c.cat_id).where(memories.c.id == mid)).scalar_one() is None


def test_cat_status_check_and_defaults():
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(insert(cats).values(user_id=MARK, name="Тестокот").returning(
            cats.c.status, cats.c.aliases, cats.c.aliases_norm, cats.c.photo_url)).one()
    assert row.status == "confirmed" and row.aliases == [] and row.aliases_norm == [] and row.photo_url is None
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(insert(cats).values(user_id=MARK, name="Х", status="weird"))


def test_cat_delete_sets_memory_cat_id_null():
    eng = get_engine()
    with eng.begin() as conn:
        cid = conn.execute(insert(cats).values(user_id=MARK, name="Тестокот").returning(cats.c.id)).scalar_one()
        mid = conn.execute(insert(memories).values(fact_text="не любит дождь", user_id=MARK, cat_id=cid)
                           .returning(memories.c.id)).scalar_one()
    with eng.begin() as conn:
        conn.execute(delete(cats).where(cats.c.id == cid))
    with eng.connect() as conn:
        row = conn.execute(select(memories.c.id, memories.c.cat_id).where(memories.c.id == mid)).one()
    assert row.id == mid and row.cat_id is None            # факт остался, связь снята


def test_memory_fk_rejects_unknown_cat():
    with pytest.raises(IntegrityError):
        with get_engine().begin() as conn:
            conn.execute(insert(memories).values(fact_text="x", user_id=MARK, cat_id=987654321))
