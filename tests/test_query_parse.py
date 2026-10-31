"""tests/test_query_parse.py — детерминированный разбор вопроса к 🧠 Памяти (core/query_parse.py).

Резолв людей имитируется тем же кодом форм, что в проде (core.people.mention_candidates / aliases_norm_for),
поэтому падежи («Мише», «Олю») и короткие имена проверяются по-настоящему. Запросы — строчными.
Privacy: вымышленные имена.
"""
from __future__ import annotations

import pytest

from core import query_parse as qp
from core.people import aliases_norm_for, mention_candidates

PEOPLE = [
    {"id": 1, "name": "Михаил Тестов", "aliases": ["Миша"]},
    {"id": 2, "name": "Миша Образцов", "aliases": ["Миша"]},
    {"id": 3, "name": "Ольга", "aliases": ["Оля"]},
    {"id": 4, "name": "Мишаня", "aliases": []},
    {"id": 5, "name": "Олег", "aliases": []},
]
for _p in PEOPLE:
    _p["norm"] = set(aliases_norm_for(_p["name"], _p["aliases"]))
    _p["birthday"] = None
    _p["birthday_year_known"] = True


def resolve(cands):
    forms = set(mention_candidates(cands))
    return [p for p in PEOPLE if p["norm"] & forms]


def plan(q, facts_words=()):
    return qp.parse(q, resolve, lambda w: w in facts_words)


def ids(p):
    return [x["id"] for x in p.people]


# ── субъект ─────────────────────────────────────────────────────────────────

def test_person_resolved_through_case_forms():
    p = plan("что любит оле")
    assert p.subject == "person" and ids(p) == [3]
    assert plan("что не любит олю").subject == "person"


def test_homonyms_are_ambiguous_not_guessed():
    p = plan("что не любит миша")
    assert p.subject == "ambiguous" and sorted(ids(p)) == [1, 2]


def test_diminutives_do_not_collapse_by_stem():
    # «мишаня» ≠ «миша», «олег» ≠ «оля/ольга»: общая основа не склеивает людей
    assert ids(plan("что любит мишаня")) == [4]
    assert ids(plan("что любит олег")) == [5]
    assert 5 not in ids(plan("что любит оля")) and 4 not in ids(plan("что любит миша"))


@pytest.mark.parametrize("q", ["что я не ем", "чего я не пью", "что мне нельзя", "что не ем", "что я не переношу"])
def test_self_subject(q):
    assert plan(q).subject == "self"


def test_self_and_person_mixed_is_unclear():
    assert plan("что я люблю из того что любит оля").subject == "unclear"


def test_unknown_person_needs_anchor_and_absence_in_facts():
    p = plan("что не любит ваня")
    assert p.subject == "unknown_person" and p.unknown_name == "ваня"
    assert plan("что любит про ваню").subject in ("unknown_person", "unclear")
    # слово встречается в фактах → это предмет, не человек
    assert plan("что любит кофе", facts_words=("кофе",)).subject == "unclear"


def test_nothing_parsed_is_unclear():
    assert plan("номер квартиры").subject == "unclear"
    assert plan("").subject == "unclear"


# ── тип ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,types", [
    ("что не любит оля", ("dislikes",)),
    ("что не ест оля", ("dislikes",)),
    ("что бесит олю", ("dislikes",)),
    ("что оле не нравится", ("dislikes",)),
    ("кого ненавидит оля", ("dislikes",)),
    ("у оли аллергия", ("important",)),
    ("что нельзя оле", ("important",)),
    ("что важно про олю", ("important",)),
    ("как у оли со здоровьем", ("important",)),
    ("что любит оля", ("likes",)),
    ("что обожает оля", ("likes",)),
    ("аллергия и что не ест оля", ("important", "dislikes")),
])
def test_types(q, types):
    assert plan(q).types == types


def test_dislike_words_never_pull_important_and_vice_versa():
    assert "important" not in plan("что не любит оля").types
    assert "dislikes" not in plan("что у оли аллергия").types


def test_no_type_for_plain_topic():
    p = plan("что оля говорила про дачу")
    assert p.subject == "person" and p.types == ()
    assert not p.structured


# ── ДР ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["когда у оли др", "когда день рождения у оли", "сколько лет оле", "когда родилась оля"])
def test_birthday_intent(q):
    p = plan(q)
    assert p.intent == "birthday" and p.subject == "person" and ids(p) == [3] and p.structured


# ── структурность ───────────────────────────────────────────────────────────

def test_structured_requires_person_and_type():
    assert plan("что любит оля").structured
    assert plan("что не любит миша").structured            # тёзки тоже структурны (два блока)
    assert not plan("что я не ем").structured              # «я» → гибрид внутри моих фактов
    assert not plan("номер квартиры").structured
