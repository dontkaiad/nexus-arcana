"""tests/test_people_accounts.py — несколько аккаунтов на человека (ADR-0027, PR-7).

Миграция с бэкфиллом (счёт до/после), репозиторий, карточка «Я», поглощение в «Я», импорт.
Фикстуры обезличены."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
from core.repos import pg_people_repo as repo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests._people_helpers import insert_person
from tools.people_import import import_memory as im
from tools.people_import.selfblock import SelfBlock

REPO = Path(__file__).resolve().parent.parent
MARK = "test-people-accounts"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def accts(pid):
    with get_engine().connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT platform, external_id, username, is_primary FROM person_accounts WHERE person_id=:p "
            "ORDER BY platform, external_id"), {"p": pid})]


# ── миграция ────────────────────────────────────────────────────────────────

def _load(pattern, name):
    path = next((REPO / "alembic" / "versions").glob(pattern))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_backfills_every_id_checks_counts_and_is_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load("*_person_accounts.py", "_mig_pa")
    assert mig.down_revision == "8e261c43803c"
    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()                                                    # вернули старые колонки
                cols = {c["name"] for c in inspect(conn).get_columns("people")}
                assert {"tg_id", "tg_username", "vk_id"} <= cols and "person_accounts" not in inspect(conn).get_table_names()
                conn.execute(text("DELETE FROM people WHERE user_id LIKE 'mig-pa-%'"))
                conn.execute(text(
                    "INSERT INTO people (user_id, name, tg_id, tg_username, vk_id) VALUES "
                    "('mig-pa-1','a',101,'nick',201), ('mig-pa-1','b',102,NULL,NULL), ('mig-pa-2','c',NULL,NULL,203),"
                    "('mig-pa-2','d',NULL,NULL,NULL)"))
                before = conn.execute(text("SELECT count(tg_id)+count(vk_id) FROM people")).scalar_one()
                mig.upgrade()                                                      # бэкфилл + сверка счёта (упадёт при расхождении)
                after = conn.execute(text("SELECT count(*) FROM person_accounts")).scalar_one()
                assert before == after
                rows = conn.execute(text(
                    "SELECT p.name, a.platform, a.external_id, a.username, a.is_primary FROM person_accounts a "
                    "JOIN people p ON p.id = a.person_id WHERE p.user_id LIKE 'mig-pa-%' ORDER BY p.name, a.platform")).all()
                assert [tuple(r) for r in rows] == [("a", "tg", 101, "nick", True), ("a", "vk", 201, None, True),
                                                     ("b", "tg", 102, None, True), ("c", "vk", 203, None, True)]
                cols = {c["name"] for c in inspect(conn).get_columns("people")}
                assert not cols & {"tg_id", "tg_username", "vk_id"}                # старые колонки убраны
                # даунгрейд восстанавливает колонки из ОСНОВНЫХ аккаунтов; лишние аккаунты теряются осознанно
                a_id = conn.execute(text("SELECT id FROM people WHERE name='a' AND user_id='mig-pa-1'")).scalar_one()
                conn.execute(text("INSERT INTO person_accounts (user_id, person_id, platform, external_id, is_primary) "
                                  "VALUES ('mig-pa-1', :p, 'tg', 999, false)"), {"p": a_id})
                mig.downgrade()
                got = conn.execute(text("SELECT tg_id, tg_username, vk_id FROM people WHERE id=:p"), {"p": a_id}).one()
                assert tuple(got) == (101, "nick", 201)
        finally:
            trans.rollback()


# ── репозиторий ─────────────────────────────────────────────────────────────

def test_create_with_accounts_and_lookup_by_account():
    pid = repo.create(MARK, "Имя", [], tg_id=5, tg_username="nick", vk_id=6)
    assert accts(pid) == [("tg", 5, "nick", True), ("vk", 6, None, True)]
    for plat, ext in (("tg", 5), ("vk", 6)):
        assert repo.find_by_account(MARK, plat, ext)["id"] == pid
    assert repo.find_by_tg(MARK, 5)["tg_username"] == "nick" and repo.find_by_vk(MARK, 6)["vk_id"] == 6
    assert repo.find_by_tg(MARK, 6) is None and repo.find_by_account("other-owner", "tg", 5) is None


def test_fill_missing_adds_only_missing_accounts_and_never_changes_primary():
    pid = repo.create(MARK, "Имя", [], tg_id=1, tg_username="first")
    p = repo.get_person(MARK, pid)
    assert repo.fill_missing(p, aliases=[], birthday=None, tg_id=2, tg_username="second", vk_id=7) is True
    assert accts(pid) == [("tg", 1, "first", True), ("tg", 2, "second", False), ("vk", 7, None, True)]
    assert repo.fill_missing(repo.get_person(MARK, pid), aliases=[], birthday=None, tg_id=2, tg_username="second", vk_id=7) is False
    other = repo.create(MARK, "Другой", [])
    repo.fill_missing(repo.get_person(MARK, other), aliases=[], birthday=None, tg_id=1, tg_username=None, vk_id=None)
    assert accts(other) == []                                              # чужой аккаунт не отбираем (ON CONFLICT DO NOTHING)


def test_self_card_gets_all_owner_accounts_once():
    sb = SelfBlock()
    sb.tg_ids |= {20, 10}
    sb.vk_ids |= {30}
    sb.usernames |= {"mynick"}
    lst = sb.account_list()
    assert [(a["platform"], a["external_id"], a["username"], a["is_primary"]) for a in lst] == [
        ("tg", 10, "mynick", True), ("tg", 20, None, False), ("vk", 30, None, True)]
    pid = repo.get_or_create_self(MARK, "Кай", ["Kai"], tg_id=10, tg_username="mynick", vk_id=30, accounts=lst)
    assert accts(pid) == [("tg", 10, "mynick", True), ("tg", 20, None, False), ("vk", 30, None, True)]
    assert repo.get_or_create_self(MARK, "Кай", ["Kai"], tg_id=10, tg_username="mynick", vk_id=30, accounts=lst) == pid
    assert accts(pid) == [("tg", 10, "mynick", True), ("tg", 20, None, False), ("vk", 30, None, True)]


def test_absorb_into_self_moves_accounts_and_future_lookup_finds_self():
    me = repo.create(MARK, "Кай", [], tg_id=10, status="confirmed", is_self=True)
    other_acc = insert_person(MARK, "Старый ник", tg_id=22, vk_id=33)
    repo.absorb_into_self(MARK, other_acc)
    assert accts(me) == [("tg", 10, None, True), ("tg", 22, None, False), ("vk", 33, None, True)]
    assert repo.find_by_tg(MARK, 22)["id"] == me                           # повторный импорт найдёт «Я», а не новую карточку


def test_primary_is_unique_per_person_and_platform_at_db_level():
    pid = repo.create(MARK, "Имя", [], tg_id=1)
    with pytest.raises(IntegrityError):
        with get_engine().begin() as c:
            c.execute(text("INSERT INTO person_accounts (user_id, person_id, platform, external_id, is_primary) "
                           "VALUES (:u, :p, 'tg', 2, true)"), {"u": MARK, "p": pid})


# ── импорт ──────────────────────────────────────────────────────────────────

def row(name, fact, type_="likes", **kw):
    base = {"name": name, "aliases": [], "fact": fact, "type": type_, "quote": "q", "source": "tg",
            "origin": "chat", "tg_id": None, "tg_username": None, "vk_id": None, "vk_screen": None}
    base.update(kw)
    return base


def test_import_creates_account_rows_and_second_account_of_existing_person_is_added(monkeypatch):
    monkeypatch.setenv("PEOPLE_IMPORT_USER_ID", MARK)
    pid = insert_person(MARK, "Имя Фамилия", tg_id=1)
    # кандидат с этим же TG и новым VK (напр. склейка VK↔TG по ДР) → VK добавится к существующему человеку
    rows = [row("Имя Фамилия", "любит чай", tg_id=1, tg_username="u1"),
            row("Имя Фамилия", "День рождения: 05.03.1990", "birthday", tg_id=1, origin="api", quote=""),
            row("Имя Фамилия", "любит кофе", source="vk", vk_id=9),
            row("Имя Фамилия", "День рождения: 05.03.1990", "birthday", source="vk", vk_id=9, origin="api", quote="")]
    monkeypatch.setattr(im, "load_staging", lambda: rows)
    asyncio.run(im.run(False, True, 20, 0))
    assert accts(pid) == [("tg", 1, "u1", True), ("vk", 9, None, True)]
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM people WHERE user_id=:u"), {"u": MARK}).scalar_one() == 1
    asyncio.run(im.run(False, True, 20, 0))                                 # идемпотентно
    assert accts(pid) == [("tg", 1, "u1", True), ("vk", 9, None, True)]
