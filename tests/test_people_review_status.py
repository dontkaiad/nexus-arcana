"""tests/test_people_review_status.py — People domain, PR-1 (ADR-0027).

Реальные INSERT/SELECT на тестовой БД (DATABASE_URL):
  (a) миграция people_domain up/down обратима, CHECK/частичные unique работают;
  (b) pending/rejected факт НЕ виден ни в ILIKE, ни в semantic, ни в контексте,
      ни в списках (/memory, mini-app) — старые (confirmed) видны как раньше;
  (c) деструктивные операции (забудь/удали) видят pending-факт.

Privacy: generic фикстуры. Все строки помечаются user_id=MARK и удаляются.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, insert, inspect, select
from sqlalchemy.exc import IntegrityError

from core import memory as mem
from core import memory_rag
from core.db import get_engine
from tests._people_helpers import insert_person
from core.repos.memories_table import memories
from core.repos.people_tables import people
from core.repos.pg_memory_repo import PgMemoryRepo

REPO = Path(__file__).resolve().parent.parent
MARK = "test-people-pr1"
TOKEN = "зюзябрикс"          # уникальный токен, которого нет в реальных данных
CAT = "👥 Люди"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id == MARK))
        conn.execute(delete(people).where(people.c.user_id == MARK))


def _ins(status: str, fact: str = None, key: str = "", **kw) -> int:
    fact = fact or f"{TOKEN} {status}"
    with get_engine().begin() as conn:
        return conn.execute(
            insert(memories).values(
                fact_text=fact, key_name=key, category=CAT, related_to=TOKEN,
                user_id=MARK, review_status=status, **kw,
            ).returning(memories.c.id)
        ).scalar_one()


@pytest.fixture
def three():
    return {s: _ins(s, key=f"person_{s}") for s in ("confirmed", "pending", "rejected")}


def _ids(rows):
    return {int(m.id) for m in rows}


# ── (a) миграция + констрейнты ───────────────────────────────────────────────

def test_migration_up_down_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = next((REPO / "alembic" / "versions").glob("*_people_domain.py"))
    spec = importlib.util.spec_from_file_location("_mig_people", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "e1f2a3b4c5d6"

    # person_accounts (PR-7) держит FK на people: откатываем её первой, как при настоящем даунгрейде цепочки
    acc_path = next((REPO / "alembic" / "versions").glob("*_person_accounts.py"))
    acc_spec = importlib.util.spec_from_file_location("_mig_accounts", acc_path)
    acc = importlib.util.module_from_spec(acc_spec)
    acc_spec.loader.exec_module(acc)

    eng = get_engine()
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                acc.downgrade()
                mig.downgrade()
                insp = inspect(conn)
                assert "people" not in insp.get_table_names()
                cols = [c["name"] for c in insp.get_columns("memories")]
                assert "person_id" not in cols and "review_status" not in cols
                mig.upgrade()
                acc.upgrade()
                insp = inspect(conn)
                assert "people" in insp.get_table_names()
                cols = [c["name"] for c in insp.get_columns("memories")]
                assert "person_id" in cols and "review_status" in cols
        finally:
            trans.rollback()


def test_existing_rows_default_to_confirmed():
    with get_engine().begin() as conn:
        mid = conn.execute(
            insert(memories).values(fact_text="x", user_id=MARK).returning(memories.c.id)
        ).scalar_one()
        status = conn.execute(
            select(memories.c.review_status).where(memories.c.id == mid)
        ).scalar_one()
    assert status == "confirmed"


def test_review_status_check_rejects_garbage():
    with pytest.raises(IntegrityError):
        _ins("maybe")


def test_people_status_check_and_account_uniques():
    eng = get_engine()
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(insert(people).values(user_id=MARK, name="X", status="weird"))
    # одинаковые имена без аккаунтов — можно (однофамильцы)
    with eng.begin() as conn:
        conn.execute(insert(people), [
            {"user_id": MARK, "name": "Одноимённый"},
            {"user_id": MARK, "name": "Одноимённый"},
        ])
    insert_person(MARK, "A", tg_id=424242)
    with pytest.raises(IntegrityError):                                   # (владелец, платформа, id) уникален
        insert_person(MARK, "B", tg_id=424242)
    insert_person(MARK, "C", vk_id=777001)
    with pytest.raises(IntegrityError):
        insert_person(MARK, "D", vk_id=777001)
    insert_person(MARK, "E", vk_id=424242)                                # тот же id на другой платформе — можно
    with pytest.raises(IntegrityError):                                   # не больше одного основного на (человек, платформа)
        insert_person(MARK, "F", accounts=[{"platform": "tg", "external_id": 1, "is_primary": True},
                                           {"platform": "tg", "external_id": 2, "is_primary": True}])
    with pytest.raises(IntegrityError):                                   # платформа только tg|vk
        insert_person(MARK, "G", accounts=[{"platform": "ok", "external_id": 3}])


def test_deleting_person_nulls_person_id():
    eng = get_engine()
    with eng.begin() as conn:
        pid = conn.execute(
            insert(people).values(user_id=MARK, name="P").returning(people.c.id)
        ).scalar_one()
    mid = _ins("confirmed", person_id=pid)
    with eng.begin() as conn:
        conn.execute(delete(people).where(people.c.id == pid))
        got = conn.execute(
            select(memories.c.person_id).where(memories.c.id == mid)
        ).scalar_one()
    assert got is None


# ── (b) чтение: только confirmed ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ilike_search_hides_pending_and_rejected(three):
    repo = PgMemoryRepo()
    for match_all in (False, True):
        got = _ids(await repo.search([TOKEN], user_id=MARK, match_all=match_all))
        assert got == {three["confirmed"]}


@pytest.mark.asyncio
async def test_search_confirmed_only_false_sees_everything(three):
    got = _ids(await PgMemoryRepo().search([TOKEN], user_id=MARK, confirmed_only=False))
    assert got == set(three.values())


@pytest.mark.asyncio
async def test_listings_hide_pending(three):
    repo = PgMemoryRepo()
    assert _ids(await repo.find_by_category(CAT, user_id=MARK)) == {three["confirmed"]}
    assert _ids(await repo.find_recent(user_id=MARK, page_size=50)) == {three["confirmed"]}
    assert _ids(await repo.find_by_key_prefixes(["person_"], user_id=MARK)) == {three["confirmed"]}
    assert _ids(await repo.find_by_exact_key("person_pending", user_id=MARK)) == set()
    assert _ids(await repo.find_by_exact_key("person_confirmed", user_id=MARK)) == {three["confirmed"]}


@pytest.mark.asyncio
async def test_find_pages_by_hint_confirmed_only_flag(three):
    seen = _ids(await mem._find_pages_by_hint(TOKEN, use_semantic=False, user_id=MARK))
    assert seen == {three["confirmed"]}
    all_ = _ids(await mem._find_pages_by_hint(
        TOKEN, use_semantic=False, user_id=MARK, confirmed_only=False))
    assert all_ == set(three.values())


def test_semantic_search_hides_pending(three, monkeypatch):
    vec = [1.0] + [0.0] * 1023
    monkeypatch.setattr(memory_rag, "_embed", lambda *a, **k: [vec])
    with get_engine().begin() as conn:
        for mid in three.values():
            conn.execute(
                sa.text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"),
                {"e": memory_rag._vec_literal(vec), "i": mid},
            )
    got = _ids(memory_rag.search_memory_semantic("что угодно", user_id=MARK))
    assert got == {three["confirmed"]}


@pytest.mark.asyncio
async def test_context_excludes_pending(three):
    with patch.object(mem, "_semantic_search_memory", AsyncMock(side_effect=lambda q, ex, **k: ex)):
        ctx = await mem.get_memories_for_context(MARK, [TOKEN], bot_label="☀️ Nexus")
    assert f"{TOKEN} confirmed" in ctx
    assert f"{TOKEN} pending" not in ctx
    assert f"{TOKEN} rejected" not in ctx


@pytest.mark.asyncio
async def test_old_rows_visible_as_before():
    """Строки без явного review_status (как до миграции) видны везде."""
    with get_engine().begin() as conn:
        mid = conn.execute(insert(memories).values(
            fact_text=f"{TOKEN} legacy", related_to=TOKEN, category=CAT,
            key_name="legacy_k", user_id=MARK,
        ).returning(memories.c.id)).scalar_one()
    repo = PgMemoryRepo()
    assert mid in _ids(await repo.search([TOKEN], user_id=MARK))
    assert mid in _ids(await repo.find_by_category(CAT, user_id=MARK))
    assert mid in _ids(await repo.find_recent(user_id=MARK, page_size=50))
    assert mid in _ids(await repo.find_by_exact_key("legacy_k", user_id=MARK))


# ── (c) деструктивные операции видят pending ─────────────────────────────────

def _msg():
    m = AsyncMock()
    m.from_user = MagicMock(id=987001)
    return m


@pytest.mark.asyncio
async def test_delete_memory_can_archive_pending_fact():
    pid = _ins("pending", fact=f"{TOKEN} единственный")
    await mem.delete_memory(_msg(), TOKEN, MARK)
    with get_engine().connect() as conn:
        archived = conn.execute(
            select(memories.c.is_archived).where(memories.c.id == pid)
        ).scalar_one()
    assert archived is True


@pytest.mark.asyncio
async def test_deactivate_memory_can_hit_pending_fact():
    pid = _ins("pending", fact=f"{TOKEN} единственный")
    await mem.deactivate_memory(_msg(), TOKEN, MARK)
    with get_engine().connect() as conn:
        current = conn.execute(
            select(memories.c.is_current).where(memories.c.id == pid)
        ).scalar_one()
    assert current is False
