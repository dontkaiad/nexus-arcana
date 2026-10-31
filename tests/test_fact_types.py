"""tests/test_fact_types.py — детерминированные типы фактов людей, ручная смена типа, display name аккаунтов.

Реальный Postgres (DATABASE_URL, миграции до head). Вымышленные имена, user_id=MARK, всё чистится.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, insert, text

from core.db import get_engine
from core.people import owner_fact, retype_key
from core.query_parse import fact_type_from_text as ft
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests._people_helpers import insert_person

MARK = "test-fact-types"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


# ── лексикон по тексту факта ────────────────────────────────────────────────

@pytest.mark.parametrize("fact,typ", [
    ("не любит ходить один", "dislikes"),
    ("совсем не любит шум", "dislikes"),
    ("терпеть не может опоздания", "dislikes"),
    ("ненавидит жару", "dislikes"),
    ("бесит громкий звук", "dislikes"),
    ("не нравится, когда перебивают", "dislikes"),
    ("не ест мясо", "dislikes"),
    ("любит острое", "likes"),
    ("обожает море", "likes"),
    ("аллергия на орехи", "important"),
    ("нельзя кофеин", "important"),
    ("диабет", "important"),
    ("важно: звонить заранее", "important"),
    ("мать ругала", None),
    ("пьёт таблетки", None),
    ("любит чай, но не любит кофе", None),     # двусмысленно → не трогаем
    ("любимый цвет — синий", None),            # «любимый» не «любит»
    ("ест много", None),
])
def test_fact_type_from_text(fact, typ):
    assert ft(fact) == typ


def test_negation_never_yields_likes():
    for t in ("не любит чай", "не очень любит людей", "ей не нравится зима"):
        assert ft(t) != "likes"


def test_retype_key():
    assert retype_key("person_7_likes_0123abcd", "dislikes") == "person_7_dislikes_0123abcd"
    assert retype_key("tz_123", "likes") is None and retype_key("person_7_likes_0123abcd", "bogus") is None


# ── БД ───────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))
        c.execute(text("DROP TABLE IF EXISTS memories_key_bak_retype"))
        c.execute(text("DROP TABLE IF EXISTS memories_prefix_bak"))


def _person(name="Ольга", aliases=("Оля",), **cols):
    pid = insert_person(MARK, name, aliases=list(aliases), status="confirmed", **cols)
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)
    return pid


def _fact(pid, typ, fact, h="aaaaaaaa", status="confirmed", **kw):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=fact, key_name="person_%d_%s_%s" % (pid, typ, h), category="👥 Люди", person_id=pid,
            user_id=MARK, review_status=status, **kw).returning(memories.c.id)).scalar_one()


def _key(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT key_name FROM memories WHERE id=:i"), {"i": mid}).scalar()


def _load_script(name):
    spec = importlib.util.spec_from_file_location("_s_" + name, SCRIPTS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run(script, *argv):
    m = _load_script(script)
    with patch.object(sys, "argv", [script, *argv]):
        return m.main()


# ── ручная смена типа ────────────────────────────────────────────────────────

def test_set_fact_type_rewrites_key_and_card_groups():
    p = _person()
    f = _fact(p, "other", "мать ругала", h="11111111")
    assert prepo.set_fact_type(MARK, p, f, "dislikes") == "ok"
    assert _key(f) == "person_%d_dislikes_11111111" % p
    assert [g["type"] for g in __import__("miniapp.backend.routes.people", fromlist=["x"])._group_facts(prepo.list_facts(p))] == ["dislikes"]


def test_set_fact_type_guards():
    p = _person(); other = _person("Вера", ("Верочка",))
    f = _fact(p, "other", "текст", h="22222222")
    tomb = _fact(p, "dislikes", "", h="33333333", status="rejected")
    clash = _fact(p, "likes", "текст", h="22222222")           # ключ person_<p>_likes_22222222 занят
    assert prepo.set_fact_type(MARK, p, f, "bogus") == "bad_type"
    assert prepo.set_fact_type(MARK, p, f, "likes") == "conflict"
    assert prepo.set_fact_type(MARK, p, tomb, "likes") == "not_found"          # tombstone не трогаем
    assert prepo.set_fact_type(MARK, other, f, "likes") == "not_found"         # чужая карточка
    assert prepo.set_fact_type(MARK, p, f, "other") == "ok"                    # тот же тип — no-op


# ── скрипт пересчёта типов ───────────────────────────────────────────────────

def test_retype_script_dry_run_changes_nothing_apply_rewrites_only_matched(capsys):
    p = _person()
    wrong = _fact(p, "likes", "не любит ходить один", h="aaaa0001")
    ok = _fact(p, "likes", "любит острое", h="aaaa0002")
    keep = _fact(p, "important", "мать ругала", h="aaaa0003")           # лексикон молчит → не трогаем
    mixed = _fact(p, "likes", "любит чай, но не любит кофе", h="aaaa0004")
    rejected = _fact(p, "likes", "не любит шум", h="aaaa0005", status="rejected")
    before = {i: _key(i) for i in (wrong, ok, keep, mixed, rejected)}

    assert _run("retype_person_facts") == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "likes" in out and "dislikes" in out
    assert {i: _key(i) for i in before} == before                      # dry-run не пишет

    assert _run("retype_person_facts", "--apply") == 0
    assert _key(wrong) == "person_%d_dislikes_aaaa0001" % p
    assert all(_key(i) == before[i] for i in (ok, keep, mixed, rejected))
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM memories_key_bak_retype")).scalar() >= 1


def test_retype_script_skips_key_conflict(capsys):
    p = _person()
    a = _fact(p, "likes", "не любит шум", h="bbbb0001")
    _fact(p, "dislikes", "не любит шум", h="bbbb0001")                 # такой ключ уже есть
    _run("retype_person_facts", "--apply")
    assert _key(a) == "person_%d_likes_bbbb0001" % p
    assert "пропуск (ключ уже занят):    1" in capsys.readouterr().out


# ── display name аккаунта ────────────────────────────────────────────────────

def _account(pid, ext, primary=False, display=None):
    with get_engine().begin() as c:
        return c.execute(text(
            "INSERT INTO person_accounts (user_id, person_id, platform, external_id, is_primary, display_name) "
            "VALUES (:u,:p,'tg',:e,:pr,:d) RETURNING id"),
            {"u": MARK, "p": pid, "e": ext, "pr": primary, "d": display}).scalar()


def test_display_name_goes_to_related_to_and_owner_fact_strips_it():
    p = _person()
    acc = _account(p, 910001, primary=True)
    f = _fact(p, "likes", "Черепашка: любит чай", h="cccc0001", related_to="Ольга | Оля")
    assert prepo.set_account_display_name(MARK, p, acc, "Черепашка") is True
    with get_engine().connect() as c:
        rel = c.execute(text("SELECT related_to FROM memories WHERE id=:i"), {"i": f}).scalar()
    assert rel == "Ольга | Оля | Черепашка"
    assert owner_fact("Черепашка: любит чай", rel, p) == "Ольга: любит чай"
    assert prepo.set_account_display_name(MARK, p, 999999999, "x") is False


def test_strip_script_dry_run_lists_candidates_and_apply_sets_name_strips_and_reembeds(capsys):
    p = _person(); other = _person("Вера", ("Верочка",))
    acc = _account(p, 910002)
    f1 = _fact(p, "likes", "Черепашка: любит чай", h="dddd0001", source_chat_id=910002, related_to="Ольга | Оля")
    f2 = _fact(p, "dislikes", "Черепашка: не любит шум", h="dddd0002", source_chat_id=910002, related_to="Ольга | Оля")
    bare = _fact(p, "likes", "любит джаз", h="dddd0003", source_chat_id=910002, related_to="Ольга | Оля")
    important = _fact(p, "important", "Важно: аллергия", h="dddd0004", related_to="Ольга | Оля")   # защищённый префикс
    foreign = _fact(other, "likes", "Черепашка: чужой", h="dddd0005", related_to="Вера | Верочка")  # другой человек

    assert _run("strip_account_prefixes") == 0
    out = capsys.readouterr().out
    assert f"person={p} prefix='Черепашка' facts=2" in out and "чат совпал" in out
    assert "Важно" not in out
    with get_engine().connect() as c:
        assert c.execute(text("SELECT fact_text FROM memories WHERE id=:i"), {"i": f1}).scalar().startswith("Черепашка:")

    embedded = []
    with patch("core.memory_rag.index_memories_batch", side_effect=lambda items: (embedded.extend(items), len(items))[1]):
        assert _run("strip_account_prefixes", "--set", f"{p}=Черепашка", "--apply") == 0
    with get_engine().connect() as c:
        rows = {r.id: r for r in c.execute(text("SELECT id, fact_text, related_to FROM memories WHERE person_id IN (:a,:b)"),
                                            {"a": p, "b": other})}
        disp = c.execute(text("SELECT display_name FROM person_accounts WHERE id=:i"), {"i": acc}).scalar()
        bak = c.execute(text("SELECT count(*) FROM memories_prefix_bak")).scalar()
    assert disp == "Черепашка" and bak == 2
    assert rows[f1].fact_text == "любит чай" and rows[f2].fact_text == "не любит шум"
    assert rows[bare].fact_text == "любит джаз" and rows[important].fact_text == "Важно: аллергия"
    assert rows[foreign].fact_text == "Черепашка: чужой"
    assert rows[f1].related_to == "Ольга | Оля | Черепашка"
    assert sorted(i["memory_id"] for i in embedded) == sorted([str(f1), str(f2)])
    assert {i["embed_text"] for i in embedded} == {"Ольга: любит чай", "Ольга: не любит шум"}


def test_strip_script_without_unique_account_skips(capsys):
    p = _person()
    _fact(p, "likes", "Инна: любит чай", h="eeee0001", source_chat_id=1)
    _fact(p, "likes", "Инна: любит кофе", h="eeee0002", source_chat_id=2)     # два разных чата, аккаунтов нет
    _run("strip_account_prefixes", "--set", f"{p}=Инна", "--apply")
    assert "однозначно не определён" in capsys.readouterr().out
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM memories WHERE person_id=:p AND fact_text LIKE 'Инна:%'"), {"p": p}).scalar() == 2
