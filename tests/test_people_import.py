"""tests/test_people_import.py — PR-2 домена People (ADR-0027): staging → people + pending-факты.

Склейка/планирование — чистые функции; запись — реальные INSERT/SELECT на тестовой БД.
Privacy: generic фикстуры (Имя1 Фамилия1 …), ничего реального.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import delete, select, text

from core.db import get_engine
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tools.people_import import import_memory as im
from tools.people_import.common import fact_hash, parse_birthday

MARK = "test-people-import-pr2"


def row(name, fact, type_="likes", source="tg", **kw):
    base = {"name": name, "aliases": [], "fact": fact, "type": type_, "quote": "q" if kw.get("origin") != "api" else "",
            "source": source, "origin": "chat", "tg_id": None, "tg_username": None,
            "vk_id": None, "vk_screen": None, "approved": None}
    base.update(kw)
    return base


# ── чистая логика ────────────────────────────────────────────────────────────

def test_parse_birthday_forms():
    assert parse_birthday("День рождения: 05.03") == (date(1904, 3, 5), False)
    assert parse_birthday("День рождения: 05.03.1990") == (date(1990, 3, 5), True)
    assert parse_birthday("др 5 марта") == (date(1904, 3, 5), False)
    assert parse_birthday("день рождения скоро") is None
    assert parse_birthday("12 числа") is None
    assert parse_birthday("30.02") is None
    assert parse_birthday("5.03 и 6.04") is None  # неоднозначно


def test_two_people_same_name_different_ids_stay_separate():
    rows = [row("Имя1 Фамилия1", "a", tg_id=1), row("Имя1 Фамилия1", "b", tg_id=2)]
    cands, _ = im.group_candidates(rows)
    assert len(cands) == 2


def test_idless_rows_attach_only_to_unique_id_candidate():
    one = [row("Имя Ф", "a", tg_id=1), row("Имя Ф", "b")]
    cands, st = im.group_candidates(one)
    assert len(cands) == 1 and len(cands[0]["facts"]) == 2

    two = [row("Имя Ф", "a", tg_id=1), row("Имя Ф", "b", tg_id=2), row("Имя Ф", "c")]
    cands, st = im.group_candidates(two)
    assert len(cands) == 3 and st["ambiguous_idless"] == 1  # не гадаем, чья «c»


def _pair(tg_bday=None, vk_bday=None, tg_user=None, vk_screen=None, vk_name="Имя1 Фамилия1"):
    rows = [row("Имя1 Фамилия1", "t-fact", tg_id=10, tg_username=tg_user)]
    if tg_bday:
        rows.append(row("Имя1 Фамилия1", tg_bday, "birthday", tg_id=10))
    rows.append(row(vk_name, "v-fact", source="vk", vk_id=20, vk_screen=vk_screen))
    if vk_bday:
        rows.append(row(vk_name, vk_bday, "birthday", source="vk", vk_id=20, origin="api"))
    return rows


def test_cross_source_merge_by_name_and_birthday():
    cands, st = im.group_candidates(_pair("др 05.03", "День рождения: 05.03.1990"))
    assert st["merged_cross"] == 1 and len(cands) == 1
    c = cands[0]
    assert c["tg_id"] == 10 and c["vk_id"] == 20


def test_cross_source_merge_by_username_equals_screen():
    cands, st = im.group_candidates(_pair(tg_user="same_nick", vk_screen="Same_Nick"))
    assert st["merged_cross"] == 1


def test_no_merge_on_name_alone_or_different_birthday_or_single_token_name():
    assert im.group_candidates(_pair())[1]["merged_cross"] == 0
    assert im.group_candidates(_pair("др 05.03", "День рождения: 06.03"))[1]["merged_cross"] == 0
    rows = [row("Имя", "a", tg_id=1, tg_username="x"), row("Имя", "b", source="vk", vk_id=2, vk_screen="x")]
    assert im.group_candidates(rows)[1]["merged_cross"] == 0   # нет фамилии


def test_no_merge_when_ambiguous_on_other_side():
    rows = _pair("др 05.03", "День рождения: 05.03.1990")
    rows += [row("Имя1 Фамилия1", "v2", source="vk", vk_id=21),
             row("Имя1 Фамилия1", "День рождения: 05.03", "birthday", source="vk", vk_id=21, origin="api")]
    assert im.group_candidates(rows)[1]["merged_cross"] == 0


def test_birthday_facts_never_become_facts_and_api_beats_chat():
    rows = [row("Имя Ф", "др 01.02", "birthday", tg_id=1),
            row("Имя Ф", "День рождения: 03.04.1999", "birthday", tg_id=1, origin="api", quote=""),
            row("Имя Ф", "любит чай", tg_id=1)]
    p = im.finalize(im.group_candidates(rows)[0][0])
    assert p["birthday"] == date(1999, 4, 3) and p["bday_origin"] == "api"
    assert [t for t, *_ in p["facts"]] == ["likes"]


def test_fact_text_prefixes_name_only_when_missing():
    assert im.fact_text("Мария Иванова", "любит чай") == "Мария: любит чай"
    assert im.fact_text("Мария Иванова", "Мария любит чай") == "Мария любит чай"


def test_key_is_by_person_id_not_slug():
    assert im.memory_key(7, "likes", "abcd1234") == "person_7_likes_abcd1234"
    assert im.memory_key(8, "likes", "abcd1234") != im.memory_key(7, "likes", "abcd1234")


# ── БД: запись, идемпотентность, pending ─────────────────────────────────────

@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setenv("PEOPLE_IMPORT_USER_ID", MARK)
    holder = {"rows": []}
    monkeypatch.setattr(im, "load_staging", lambda: holder["rows"])
    yield holder
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def _run(dry=False):
    return asyncio.run(im.run(dry, True, 20, 0))


def _state():
    with get_engine().connect() as c:
        ppl = c.execute(select(people).where(people.c.user_id == MARK).order_by(people.c.id)).all()
        mem = c.execute(select(memories).where(memories.c.user_id == MARK).order_by(memories.c.id)).all()
    return ppl, mem


def test_import_creates_pending_people_and_pending_facts(staging):
    staging["rows"] = [
        row("Имя1 Фамилия1", "любит чай", tg_id=1, aliases=["Имя1"], tg_username="nick1"),
        row("Имя1 Фамилия1", "аллергия на орехи", "important", tg_id=1),
        row("Имя1 Фамилия1", "День рождения: 05.03.1990", "birthday", tg_id=1, origin="api", quote=""),
        row("Имя2 Фамилия2", "ничего ценного", "birthday", tg_id=2),    # ДР не распознан, фактов нет → пропуск
    ]
    assert _run() == 0
    ppl, mem = _state()
    assert len(ppl) == 1 and ppl[0].status == "pending" and ppl[0].tg_id == 1
    assert ppl[0].birthday == date(1990, 3, 5)
    assert "@nick1" in ppl[0].aliases and "Имя1" in ppl[0].aliases
    assert len(mem) == 2
    for m in mem:
        assert m.review_status == "pending" and m.person_id == ppl[0].id
        assert m.category == "👥 Люди" and m.source == "auto" and m.scope == "nexus"
        assert m.key_name.startswith("person_%d_" % ppl[0].id)
        assert "Имя1 Фамилия1" in m.related_to and "@nick1" in m.related_to
        assert "рожд" not in m.fact_text.lower()      # ДР-факты в memories не пишем


def test_rerun_is_idempotent_and_dry_run_writes_nothing(staging):
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1),
                       row("Имя1 Фамилия1", "не ест рыбу", "dislikes", tg_id=1)]
    assert _run(dry=True) == 0
    assert _state() == ([], [])
    _run()
    ppl1, mem1 = _state()
    _run()
    ppl2, mem2 = _state()
    assert len(ppl2) == len(ppl1) == 1 and len(mem2) == len(mem1) == 2
    assert [m.id for m in mem2] == [m.id for m in mem1]


def test_rerun_does_not_revert_approved_fact_to_pending(staging):
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1)]
    _run()
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET review_status='confirmed' WHERE user_id=:u"), {"u": MARK})
    _run()
    assert [m.review_status for m in _state()[1]] == ["confirmed"]


def test_same_name_different_tg_ids_make_two_people_with_distinct_keys(staging):
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1), row("Имя1 Фамилия1", "любит чай", tg_id=2)]
    _run()
    ppl, mem = _state()
    assert len(ppl) == 2 and len({m.key_name for m in mem}) == 2


def test_chat_birthday_fills_only_when_empty_and_does_not_overwrite(staging):
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1),
                       row("Имя1 Фамилия1", "др 01.02", "birthday", tg_id=1)]
    _run()
    assert _state()[0][0].birthday == date(1904, 2, 1)
    assert _state()[0][0].birthday_year_known is False        # год неизвестен → флаг, не только заглушка
    with get_engine().begin() as c:
        c.execute(text("UPDATE people SET birthday='1990-06-07' WHERE user_id=:u"), {"u": MARK})
    staging["rows"][1] = row("Имя1 Фамилия1", "День рождения: 03.04.1999", "birthday", tg_id=1, origin="api", quote="")
    _run()
    assert _state()[0][0].birthday == date(1990, 6, 7)        # ручная правка не затёрта


def test_idless_person_is_matched_by_name_on_rerun(staging):
    staging["rows"] = [row("Имя Ф", "любит чай")]
    _run(); _run()
    assert len(_state()[0]) == 1


def test_confirmed_visible_count_unchanged_by_import(staging):
    with get_engine().begin() as c:
        c.execute(memories.insert().values(fact_text="старое", user_id=MARK))
    before = im.counts(MARK)["confirmed_visible"]
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1)]
    assert _run() == 0
    assert im.counts(MARK) == {"confirmed_visible": before, "pending": 1}


# ── эмбеддинги пачками ───────────────────────────────────────────────────────

def test_embed_missing_batches_and_paces(staging, monkeypatch):
    staging["rows"] = [row("Имя%d Ф" % i, "факт%d" % i, tg_id=100 + i) for i in range(5)]
    _run()
    ids = [str(m.id) for m in _state()[1]]
    calls, sleeps = [], []
    monkeypatch.setattr(im, "index_memories_batch", lambda items: calls.append(len(items)) or len(items))
    monkeypatch.setattr(im.time, "sleep", lambda s: sleeps.append(s))
    done, total = im.embed_missing(ids, 2, 21.0)
    assert (done, total) == (5, 5) and calls == [2, 2, 1] and sleeps == [21.0, 21.0]


def test_import_stores_quote_and_rerun_fills_missing_quote_without_touching_embedding(staging):
    from core import memory_rag
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1, quote="")]
    _run()
    assert _state()[1][0].source_quote is None
    vec = memory_rag._vec_literal([1.0] + [0.0] * 1023)
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE user_id=:u"), {"e": vec, "u": MARK})
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1, quote="цитата из чата")]
    _run()
    ppl, mem = _state()
    assert len(mem) == 1 and mem[0].source_quote == "цитата из чата"
    with get_engine().connect() as c:
        assert c.execute(text("SELECT embedding IS NOT NULL FROM memories WHERE user_id=:u"), {"u": MARK}).scalar_one()
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1, quote="другая цитата")]
    _run()
    assert _state()[1][0].source_quote == "цитата из чата"      # первая цитата не затирается


def test_new_import_saves_quote(staging):
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1, quote="так и сказал")]
    _run()
    assert _state()[1][0].source_quote == "так и сказал"


def test_rerun_is_idempotent_when_another_idless_person_has_a_name_equal_to_the_alias(staging):
    # В БД уже есть посторонний id-less «Имя1», а у кандидата «Имя1 Ф» алиас «Имя1». Раньше
    # сопоставление по алиасам давало 2 попадания и повторный импорт плодил дубль «Имя1 Ф».
    with get_engine().begin() as c:
        c.execute(people.insert().values(user_id=MARK, name="Имя1", aliases=[]))
    staging["rows"] = [row("Имя1 Ф", "любит чай", aliases=["Имя1"]),
                       row("Имя1 Ф", "аллергия на орехи", "important", aliases=["Имя1"])]
    _run()
    ppl1, mem1 = _state()
    assert len(ppl1) == 2 and len(mem1) == 2
    _run(); _run()
    ppl2, mem2 = _state()
    assert [p.id for p in ppl2] == [p.id for p in ppl1] and [m.id for m in mem2] == [m.id for m in mem1]
    assert {m.person_id for m in mem2} == {next(p.id for p in ppl2 if p.name == "Имя1 Ф")}


def test_rerun_keeps_same_name_idless_people_from_two_sources_separate(staging):
    staging["rows"] = [row("Имя Ф", "любит чай", source="tg"), row("Имя Ф", "не ест рыбу", "dislikes", source="vk")]
    _run()
    ppl1, mem1 = _state()
    assert len(ppl1) == 2                                          # тёзки без id не склеиваются
    owner = {m.fact_text: m.person_id for m in mem1}
    _run(); _run()
    ppl2, mem2 = _state()
    assert len(ppl2) == 2 and len(mem2) == 2
    assert {m.fact_text: m.person_id for m in mem2} == owner       # факты остались у «своих» людей
