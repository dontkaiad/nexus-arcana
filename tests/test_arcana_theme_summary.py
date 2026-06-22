"""tests/test_arcana_theme_summary.py — split события/темы (#165).

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL), без grep-оценок:
  (a) миграция u1v2w3x4y5z6 up/down обратима (в транзакции, rollback);
  (b) группировки (name,client,date) и (name,client) дают разные множества;
  (c) пополнение существующей темы обнуляет theme_summary всей группы;
  (d) запись theme_summary НЕ затирает session_summary события.

Все строки помечаются user_notion_id=MARK и удаляются после каждого теста.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, inspect, select

from core.db import get_engine
from arcana.repos.sessions_tables import sessions
from arcana.repos.pg_sessions_repo import PgSessionsRepo

REPO = Path(__file__).resolve().parent.parent
MARK = "test-theme-165"


@pytest.fixture
def repo():
    return PgSessionsRepo()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.user_notion_id == MARK))


def _ins(conn, sname, d, q, *, ssum=None, tsum=None, client_id=None):
    return conn.execute(
        insert(sessions).values(
            title=q or "t", question=q, occurred_at=d,
            session_name=sname, client_id=client_id,
            user_notion_id=MARK, archived=False,
            session_summary=ssum, theme_summary=tsum,
        ).returning(sessions.c.id)
    ).scalar_one()


# ── (a) миграция up/down ──────────────────────────────────────────────────────

def test_migration_theme_summary_up_down_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = os.path.join(
        REPO, "alembic", "versions", "u1v2w3x4y5z6_sessions_theme_summary.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_theme_165", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "t0u1v2w3x4y5"

    eng = get_engine()
    # head применён → колонка есть
    assert "theme_summary" in [c["name"] for c in inspect(eng).get_columns("sessions")]

    with eng.connect() as conn:
        trans = conn.begin()
        try:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.downgrade()
                cols = [c["name"] for c in inspect(conn).get_columns("sessions")]
                assert "theme_summary" not in cols, "downgrade не удалил колонку"
                mig.upgrade()
                cols = [c["name"] for c in inspect(conn).get_columns("sessions")]
                assert "theme_summary" in cols, "upgrade не вернул колонку"
        finally:
            trans.rollback()  # не трогаем общую dev-БД


# ── (b) группировки дают разные множества ─────────────────────────────────────

def test_grouping_theme_vs_event_are_distinct():
    sname = "ТемаB-165"
    eng = get_engine()
    with eng.begin() as conn:
        _ins(conn, sname, date(2026, 6, 1), "1) a")
        _ins(conn, sname, date(2026, 6, 1), "2) b")
        _ins(conn, sname, date(2026, 6, 2), "1) c")

    with eng.connect() as conn:
        rows = conn.execute(
            select(
                sessions.c.session_name, sessions.c.client_id, sessions.c.occurred_at
            ).where(sessions.c.user_notion_id == MARK)
        ).all()

    theme_keys = {(r.session_name, r.client_id) for r in rows}            # ТЕМА
    event_keys = {(r.session_name, r.client_id, r.occurred_at) for r in rows}  # СОБЫТИЕ
    assert len(theme_keys) == 1, "тема должна быть одна"
    assert len(event_keys) == 2, "события (по дням) — два"
    assert theme_keys != event_keys


# ── (c) пополнение темы обнуляет theme_summary ───────────────────────────────

@pytest.mark.asyncio
async def test_adding_triplet_nulls_theme_summary(repo):
    sname = "ТемаC-165"
    eng = get_engine()
    with eng.begin() as conn:
        _ins(conn, sname, date(2026, 6, 1), "1) q", tsum="старая кросс-дневная сводка")

    # тема существовала ДО новой отправки
    assert await repo.session_group_exists(sname, None, MARK) is True

    # «добавили триплет» (новый день) + инвалидация (как делает create-флоу)
    with eng.begin() as conn:
        _ins(conn, sname, date(2026, 6, 2), "1) q2")
    cleared = await repo.clear_theme_summary(sname, None)
    assert cleared >= 1

    with eng.connect() as conn:
        vals = conn.execute(
            select(sessions.c.theme_summary).where(sessions.c.user_notion_id == MARK)
        ).scalars().all()
    assert all(v is None for v in vals), "theme_summary должен обнулиться у всей группы"


# ── (d) запись темы не трогает session_summary ───────────────────────────────

@pytest.mark.asyncio
async def test_set_theme_does_not_overwrite_session_summary(repo):
    eng = get_engine()
    with eng.begin() as conn:
        sid = _ins(conn, "ТемаD-165", date(2026, 6, 1), "q", ssum="саммари события")

    ok = await repo.set_theme_summary(str(sid), "кросс-дневная сводка темы")
    assert ok is True

    with eng.connect() as conn:
        row = conn.execute(
            select(sessions.c.session_summary, sessions.c.theme_summary)
            .where(sessions.c.id == sid)
        ).first()
    assert row.session_summary == "саммари события", "session_summary затёрт!"
    assert row.theme_summary == "кросс-дневная сводка темы"
