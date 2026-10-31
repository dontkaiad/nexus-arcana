"""tests/test_people_alias_phrase_only.py — многословный «другой алиас» матчится только фразой целиком.

Регрессия: алиас-описание «муж Оли» индексировался по словам и откликался на «Оля» (ложные тёзки).
Имя человека из нескольких слов по-прежнему ищется и по отдельным словам.
Реальный Postgres; вымышленные имена; всё помечено user_id=MARK.
Privacy: generic фикстуры.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import delete, text

from core.db import get_engine
from core.people import alias_forms, aliases_norm_for, mention_candidates
from core.repos import pg_people_repo as prepo
from core.repos.people_tables import people
from tests._people_helpers import insert_person

MARK = "test-alias-phrase"
MIG = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "d2f6b8a0c317_people_multiword_alias_phrase_only.py"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        c.execute(delete(people).where(people.c.user_id == MARK))


def mk(name, aliases=(), **cols):
    pid = insert_person(MARK, name, aliases=list(aliases), status="confirmed", **cols)
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)
    return pid


def ids(*mentions):
    return [p["id"] for p in prepo.resolve(MARK, list(mentions))]


# ── чистые формы ─────────────────────────────────────────────────────────────

def test_multiword_alias_is_phrase_only():
    assert alias_forms("муж Оли") == ["муж =оли"]
    assert alias_forms("Маша с работы") == ["=маша с работы"] or len(alias_forms("Маша с работы")) == 1
    assert alias_forms("Оля") == ["=оля", "=оле", "=оли", "=олю", "=олей", "=олею"]    # однословный — как раньше
    assert len(alias_forms("сестра Маши", phrase_only=False)) > 1                       # старое поведение — для отката


@pytest.mark.parametrize("alias", ["муж Оли", "сестра Маши", "Маша с работы", "мама Кати"])
def test_description_alias_never_matches_its_inner_name(alias):
    forms = set(aliases_norm_for("Тестовый Человек", [alias]))
    for inner in ("Оли", "Оля", "Маши", "Маша", "Маше", "Кати", "Катя"):
        assert not (forms & set(mention_candidates([inner]))), (alias, inner)


def test_multiword_name_still_matches_by_single_word():
    forms = set(aliases_norm_for("Миша Ари", []))
    assert forms & set(mention_candidates(["Миша"])) and forms & set(mention_candidates(["Ари"]))


# ── резолв на реальной БД ────────────────────────────────────────────────────

def test_husband_alias_does_not_resolve_to_olya():
    olya = mk("Ольга", ["Оля"])
    husband = mk("Антон Тестов", ["муж Оли"])
    assert ids("Оли") == [olya] and ids("оля") == [olya] and ids("Олю") == [olya]
    assert husband not in ids("Оли", "Оля")


def test_phrase_alias_resolves_on_whole_phrase():
    mk("Ольга", ["Оля"])
    husband = mk("Антон Тестов", ["муж Оли"])
    assert husband in ids("муж Оли")
    assert husband not in ids("муж")


def test_other_descriptive_aliases_do_not_breed_homonyms():
    masha = mk("Мария", ["Маша"])
    sister = mk("Дарья", ["сестра Маши"])
    colleague = mk("Игорь", ["Маша с работы"])
    assert ids("Маше") == [masha]
    assert ids("сестра Маши") and sister in ids("сестра Маши")
    assert colleague not in ids("Маша")


def test_merge_keeps_source_first_name_resolvable():
    dst = mk("Маша Один")
    src = mk("Вера Пять", ["Верунчик"])
    prepo.merge_people(MARK, src, dst)
    assert ids("Вере") == [dst] and ids("Верунчику") == [dst]


# ── миграция: пересчёт и откат ───────────────────────────────────────────────

def _mig():
    spec = importlib.util.spec_from_file_location("_mig_phrase", MIG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_recomputes_leaky_forms_and_downgrade_restores_old():
    olya = mk("Ольга", ["Оля"])
    husband = mk("Антон Тестов", ["муж Оли"])
    mig = _mig()
    eng = get_engine()
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            mig._recompute(conn, False)                    # как до фикса
            assert "=оли" in conn.execute(text("SELECT aliases_norm FROM people WHERE id=:i"), {"i": husband}).scalar()
            mig._recompute(conn, True)                     # upgrade
            norm = conn.execute(text("SELECT aliases_norm FROM people WHERE id=:i"), {"i": husband}).scalar()
            assert "=оли" not in norm and "муж =оли" in norm
            assert "=оли" in conn.execute(text("SELECT aliases_norm FROM people WHERE id=:i"), {"i": olya}).scalar()
            mig._recompute(conn, False)                    # downgrade
            assert "=оли" in conn.execute(text("SELECT aliases_norm FROM people WHERE id=:i"), {"i": husband}).scalar()
        finally:
            trans.rollback()
