"""tests/test_arcana_subject_id.py — subject_id: канонический субъект темы (#189).

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL), без grep-оценок:
  (a) миграция z6a7b8c9d0e1 up/down обратима (в транзакции, rollback);
  (b) set_subject проставляет subject_id только на переданные id;
  (c) group_subject_id находит уже подтверждённый subject_id темы (ilike
      session_name+client), None если ни одна строка группы его не несёт;
  (d) list_by_subject собирает сессии с РАЗНЫМИ session_name (та самая
      фрагментация — «Вадим» / «Вадим — диагностика» / …), если у них
      общий subject_id — это и есть фикс #189.

Все строки помечаются user_notion_id=MARK и удаляются после каждого теста.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, inspect, select

from core.db import get_engine
from arcana.repos.sessions_tables import sessions
from arcana.repos.pg_sessions_repo import PgSessionsRepo
from core.repos.memories_table import memories

REPO = Path(__file__).resolve().parent.parent
MARK = "test-subject-189"


@pytest.fixture
def repo():
    return PgSessionsRepo()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.user_notion_id == MARK))
        conn.execute(delete(memories).where(memories.c.user_notion_id == MARK))


def _ins_session(conn, sname, *, client_id=None, subject_id=None, q="q"):
    return conn.execute(
        insert(sessions).values(
            title=q, question=q, occurred_at=None,
            session_name=sname, client_id=client_id, subject_id=subject_id,
            user_notion_id=MARK, archived=False,
        ).returning(sessions.c.id)
    ).scalar_one()


def _ins_memory(conn, fact="Вадим — бывший, тревожит", related_to="вадим"):
    return conn.execute(
        insert(memories).values(
            fact_text=fact, related_to=related_to, category="👥 Люди",
            user_notion_id=MARK,
        ).returning(memories.c.id)
    ).scalar_one()


# ── (a) миграция up/down ──────────────────────────────────────────────────────

def test_migration_subject_id_up_down_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = os.path.join(
        REPO, "alembic", "versions", "z6a7b8c9d0e1_sessions_subject_id.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_subject_189", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "y5z6a7b8c9d0"

    eng = get_engine()
    assert "subject_id" in [c["name"] for c in inspect(eng).get_columns("sessions")]

    with eng.connect() as conn:
        trans = conn.begin()
        try:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.downgrade()
                cols = [c["name"] for c in inspect(conn).get_columns("sessions")]
                assert "subject_id" not in cols, "downgrade не удалил колонку"
                mig.upgrade()
                cols = [c["name"] for c in inspect(conn).get_columns("sessions")]
                assert "subject_id" in cols, "upgrade не вернул колонку"
        finally:
            trans.rollback()


# ── (b) set_subject ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_subject_updates_only_given_ids(repo):
    with get_engine().begin() as conn:
        mem_id = _ins_memory(conn)
        sid1 = _ins_session(conn, "Вадим", client_id=None)
        sid2 = _ins_session(conn, "Вадим — другая сессия", client_id=None)
        untouched = _ins_session(conn, "Другая тема", client_id=None)

    n = await repo.set_subject([str(sid1), str(sid2)], mem_id)
    assert n == 2

    with get_engine().connect() as conn:
        rows = {
            r.id: r.subject_id
            for r in conn.execute(
                select(sessions.c.id, sessions.c.subject_id)
                .where(sessions.c.id.in_([sid1, sid2, untouched]))
            )
        }
    assert rows[sid1] == mem_id
    assert rows[sid2] == mem_id
    assert rows[untouched] is None


# ── (c) group_subject_id ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_group_subject_id_found_after_confirm(repo):
    with get_engine().begin() as conn:
        mem_id = _ins_memory(conn)
        _ins_session(conn, "Вадим — отношения", client_id=None, subject_id=mem_id)

    found = await repo.group_subject_id("Вадим — отношения", None, MARK)
    assert found == mem_id


@pytest.mark.asyncio
async def test_group_subject_id_none_when_not_confirmed_yet(repo):
    with get_engine().begin() as conn:
        _ins_session(conn, "Маша — диагностика", client_id=None)

    found = await repo.group_subject_id("Маша — диагностика", None, MARK)
    assert found is None


# ── (d) list_by_subject — фикс фрагментации #189 ─────────────────────────────

@pytest.mark.asyncio
async def test_list_by_subject_collapses_different_session_names(repo):
    """Ровно баг из issue: «Вадим» / «Вадим — отношения» /
    «Вадим — диагностика» / «Вадим — диагностика ситуации» — 4 разные строки
    session_name, но один subject_id → должны вернуться ВСЕ четыре."""
    with get_engine().begin() as conn:
        mem_id = _ins_memory(conn)
        ids = [
            _ins_session(conn, "Вадим", subject_id=mem_id),
            _ins_session(conn, "Вадим — отношения", subject_id=mem_id),
            _ins_session(conn, "Вадим — диагностика", subject_id=mem_id),
            _ins_session(conn, "Вадим — диагностика ситуации", subject_id=mem_id),
        ]
        # шум: другая тема, без subject_id — не должна попасть в выдачу.
        _ins_session(conn, "Не Вадим")

    got = await repo.list_by_subject(mem_id, MARK)
    assert {t.id for t in got} == {str(i) for i in ids}
    assert all(t.subject_id == mem_id for t in got)
