"""tests/test_pg_lists_repo.py — unit tests for new PgNexusListsRepo /
PgArcanaInventoryRepo methods: get_items_for_works, get_items_for_task,
get_items_for_works (arcana), get_open_barter.

Uses in-memory SQLite with StaticPool (same trick as test_pg_works_repo.py).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from core.repos.pg_nexus_lists_repo import (
    BARTER_CATEGORY,
    InventoryItem,
    ListItem,
    PgArcanaInventoryRepo,
    PgNexusListsRepo,
)


# ── Shared engine helpers ─────────────────────────────────────────────────────

def _make_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE nexus_lists ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "notion_id TEXT, name TEXT NOT NULL, list_type TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'not_started', "
            "category TEXT DEFAULT '', quantity REAL, note TEXT DEFAULT '', "
            "price_actual REAL, price_plan REAL, store TEXT DEFAULT '', "
            "priority TEXT DEFAULT '', group_name TEXT DEFAULT '', "
            "is_recurring INTEGER DEFAULT 0, remind_days INTEGER, "
            "expires_at DATE, stage INTEGER, "
            "task_id TEXT DEFAULT '', works_id TEXT DEFAULT '', "
            "user_notion_id TEXT DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE arcana_inventory ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "notion_id TEXT, name TEXT NOT NULL, list_type TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'not_started', "
            "category TEXT DEFAULT '', quantity REAL, note TEXT DEFAULT '', "
            "group_name TEXT DEFAULT '', "
            "is_recurring INTEGER DEFAULT 0, remind_days INTEGER, "
            "expires_at DATE, works_id TEXT DEFAULT '', "
            "user_notion_id TEXT DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    return eng


def _ins_nexus(engine, name="item", works_id="", task_id="",
               user="u1", status="not_started", category=""):
    with engine.begin() as conn:
        r = conn.execute(sa.text(
            "INSERT INTO nexus_lists (name, list_type, status, works_id, task_id, "
            "user_notion_id, category) VALUES (:n, 'чеклист', :s, :w, :t, :u, :c) "
            "RETURNING id"
        ), {"n": name, "s": status, "w": works_id, "t": task_id, "u": user, "c": category})
        return str(r.fetchone()[0])


def _ins_arcana(engine, name="item", works_id="", user="u1",
                status="not_started", category=""):
    with engine.begin() as conn:
        r = conn.execute(sa.text(
            "INSERT INTO arcana_inventory (name, list_type, status, works_id, "
            "user_notion_id, category) VALUES (:n, 'чеклист', :s, :w, :u, :c) "
            "RETURNING id"
        ), {"n": name, "s": status, "w": works_id, "u": user, "c": category})
        return str(r.fetchone()[0])


# ── PgNexusListsRepo.get_items_for_works ─────────────────────────────────────

@pytest.mark.asyncio
async def test_nexus_get_items_for_works_returns_matching(tmp_path):
    eng = _make_engine()
    iid = _ins_nexus(eng, name="Задача А", works_id="7", user="u1")
    _ins_nexus(eng, name="Другая", works_id="9", user="u1")

    repo = PgNexusListsRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_works(["7"], "u1")

    assert len(items) == 1
    assert items[0].id == iid
    assert items[0].name == "Задача А"
    assert isinstance(items[0], ListItem)


@pytest.mark.asyncio
async def test_nexus_get_items_for_works_empty_ids_returns_empty(tmp_path):
    eng = _make_engine()
    _ins_nexus(eng, works_id="7")
    repo = PgNexusListsRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_works([], "u1")
    assert items == []


@pytest.mark.asyncio
async def test_nexus_get_items_for_works_excludes_archived(tmp_path):
    eng = _make_engine()
    _ins_nexus(eng, name="Архив", works_id="5", status="archived")
    _ins_nexus(eng, name="Активный", works_id="5", status="not_started")
    repo = PgNexusListsRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_works(["5"], "")
    assert len(items) == 1
    assert items[0].name == "Активный"


# ── PgNexusListsRepo.get_items_for_task ──────────────────────────────────────

@pytest.mark.asyncio
async def test_nexus_get_items_for_task_returns_matching(tmp_path):
    eng = _make_engine()
    iid = _ins_nexus(eng, name="Чеклист задачи", task_id="task-abc")
    _ins_nexus(eng, name="Другой", task_id="task-xyz")
    repo = PgNexusListsRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_task("task-abc", "")
    assert len(items) == 1
    assert items[0].id == iid


@pytest.mark.asyncio
async def test_nexus_get_items_for_task_empty_task_id_returns_empty(tmp_path):
    eng = _make_engine()
    # task_id = "" — no match expected for "unknown"
    _ins_nexus(eng, task_id="t1")
    repo = PgNexusListsRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_task("unknown-task", "")
    assert items == []


# ── PgArcanaInventoryRepo.get_items_for_works ─────────────────────────────────

@pytest.mark.asyncio
async def test_arcana_get_items_for_works_returns_matching(tmp_path):
    eng = _make_engine()
    iid = _ins_arcana(eng, name="Зажечь свечу", works_id="42")
    _ins_arcana(eng, name="Другое", works_id="99")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_works(["42"], "")
    assert len(items) == 1
    assert items[0].id == iid
    assert isinstance(items[0], InventoryItem)


@pytest.mark.asyncio
async def test_arcana_get_items_for_works_multi_ids(tmp_path):
    eng = _make_engine()
    _ins_arcana(eng, name="A", works_id="1")
    _ins_arcana(eng, name="B", works_id="2")
    _ins_arcana(eng, name="C", works_id="3")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_works(["1", "3"], "")
    assert len(items) == 2
    names = {i.name for i in items}
    assert names == {"A", "C"}


@pytest.mark.asyncio
async def test_arcana_get_items_for_works_excludes_archived(tmp_path):
    eng = _make_engine()
    _ins_arcana(eng, name="Архив", works_id="10", status="archived")
    _ins_arcana(eng, name="OK", works_id="10", status="not_started")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_items_for_works(["10"], "")
    assert len(items) == 1
    assert items[0].name == "OK"


# ── PgArcanaInventoryRepo.get_open_barter ─────────────────────────────────────

@pytest.mark.asyncio
async def test_arcana_get_open_barter_returns_barter_items(tmp_path):
    eng = _make_engine()
    iid = _ins_arcana(eng, name="Колода таро", category=BARTER_CATEGORY)
    _ins_arcana(eng, name="Свеча", category="🕯️ Расходники")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_open_barter("")
    assert len(items) == 1
    assert items[0].id == iid
    assert items[0].category == BARTER_CATEGORY


@pytest.mark.asyncio
async def test_arcana_get_open_barter_excludes_done_and_archived(tmp_path):
    eng = _make_engine()
    _ins_arcana(eng, name="Готово", category=BARTER_CATEGORY, status="done")
    _ins_arcana(eng, name="Архив", category=BARTER_CATEGORY, status="archived")
    iid = _ins_arcana(eng, name="Открытый", category=BARTER_CATEGORY, status="not_started")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_open_barter("")
    assert len(items) == 1
    assert items[0].id == iid


@pytest.mark.asyncio
async def test_arcana_get_open_barter_filters_by_user(tmp_path):
    eng = _make_engine()
    iid = _ins_arcana(eng, name="Мой бартер", category=BARTER_CATEGORY, user="u1")
    _ins_arcana(eng, name="Чужой", category=BARTER_CATEGORY, user="u2")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_open_barter("u1")
    assert len(items) == 1
    assert items[0].id == iid


@pytest.mark.asyncio
async def test_arcana_get_open_barter_includes_empty_owner(tmp_path):
    eng = _make_engine()
    # empty owner = legacy item without user binding
    iid = _ins_arcana(eng, name="Легаси", category=BARTER_CATEGORY, user="")
    my_iid = _ins_arcana(eng, name="Мой", category=BARTER_CATEGORY, user="u1")
    repo = PgArcanaInventoryRepo()
    with patch("core.repos.pg_nexus_lists_repo._get_engine", return_value=eng):
        items = await repo.get_open_barter("u1")
    ids = {i.id for i in items}
    assert iid in ids
    assert my_iid in ids
