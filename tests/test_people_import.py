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
    assert [f["type"] for f in p["facts"]] == ["likes"]


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



def test_new_import_saves_quote(staging):
    staging["rows"] = [row("Имя1 Фамилия1", "любит чай", tg_id=1, quote="так и сказал")]
    _run()
    assert _state()[1][0].source_quote == "так и сказал"




# ── PR-6b: «Я», пропуск без id, происхождение, дедуп ────────────────────────

from datetime import date as _date  # noqa: E402
from tools.people_import.selfblock import SelfBlock, load_selfblock  # noqa: E402


def _sb(**kw):
    sb = SelfBlock()
    sb.add_names(["Кай", "Kai", "Kai Lark", "Вы"] + kw.get("names", []))
    sb.tg_ids |= set(kw.get("tg", []))
    sb.vk_ids |= set(kw.get("vk", []))
    sb.usernames |= set(kw.get("users", []))
    return sb


def test_rows_without_id_never_create_people():
    cands, st = im.group_candidates([row("Имя Ф", "любит чай")], _sb())
    assert cands == [] and st["skipped_no_id"] == 1


def test_self_by_about_name_or_ids_goes_to_single_self_candidate():
    sb = _sb(names=["Свойимя"], tg=[777], vk=[888])
    rows = [row("Кай", "аллергия на пыль", "important", about="kai"),
            row("Имя Ф", "любит кошек", about="kai", tg_id=1),            # о Кай, сказано в чужом чате
            row("Свойимя", "любит чай", tg_id=5),                         # чат по имени владельца
            row("Чужое", "любит кофе", tg_id=777),                        # второй аккаунт владельца по id
            row("Чужое2", "любит йогурт", source="vk", vk_id=888),
            row("Имя Ф", "любит джаз", tg_id=1)]
    cands, _ = im.group_candidates(rows, sb)
    selfs = [c for c in cands if c["is_self"]]
    assert len(selfs) == 1 and len(selfs[0]["facts"]) == 5
    assert [c for c in cands if not c["is_self"]][0]["tg_id"] == 1
    assert len(cands) == 2                                               # «Я» и один человек


def test_self_card_is_created_once_confirmed_and_facts_attach_to_it(staging, monkeypatch):
    sb = _sb(names=["Свойимя"], tg=[777])
    monkeypatch.setattr(im, "load_selfblock", lambda *a, **k: sb)
    staging["rows"] = [row("Кай", "аллергия на пыль", "important", about="kai", source_chat_id=11,
                           source_message_date="2022-05-01", quote_author="kai"),
                       row("Имя Ф", "любит джаз", tg_id=1)]
    _run(); _run()
    ppl, mem = _state()
    me = [p for p in ppl if p.is_self]
    assert len(ppl) == 2 and len(me) == 1 and me[0].status == "confirmed" and me[0].name == "Кай"
    assert "Свойимя" in me[0].aliases and "Kai" in me[0].aliases
    self_facts = [m for m in mem if m.person_id == me[0].id]
    assert len(self_facts) == 1 and self_facts[0].review_status == "pending"
    assert self_facts[0].related_to.startswith("Кай | ")


def test_second_self_card_is_impossible():
    from sqlalchemy.exc import IntegrityError
    with get_engine().begin() as c:
        c.execute(people.insert().values(user_id=MARK, name="Кай", is_self=True))
    try:
        with pytest.raises(IntegrityError):
            with get_engine().begin() as c:
                c.execute(people.insert().values(user_id=MARK, name="Кай2", is_self=True))
    finally:
        with get_engine().begin() as c:
            c.execute(delete(people).where(people.c.user_id == MARK))


def test_provenance_and_mentions_are_stored(staging):
    staging["rows"] = [row("Имя Ф", "любит чай", tg_id=1, source_chat_id=1, source_message_date="2021-03-12",
                           quote_author="partner", quote="люблю чай")]
    _run()
    m = _state()[1][0]
    assert (m.source_chat_id, m.source_message_date, m.quote_author, m.source_platform) == (1, _date(2021, 3, 12), "partner", "tg")
    assert (m.mention_count, m.first_mention_date, m.last_mention_date) == (1, _date(2021, 3, 12), _date(2021, 3, 12))


def _vec(*xs):
    return list(xs)


def test_cluster_facts_by_cosine_same_type_and_polarity():
    def f(t, text, d):
        return {"type": t, "fact": text, "date": _date.fromisoformat(d)}
    facts = [f("likes", "любит чай", "2020-01-01"), f("likes", "обожает чай", "2023-01-01"),
             f("dislikes", "не любит чай", "2022-01-01"), f("likes", "не пьёт чай", "2022-06-01"),
             f("likes", "любит футбол", "2021-01-01")]
    # почти одинаковые векторы у всех «чайных»; футбол — другой
    vecs = [_vec(1, 0.01), _vec(1, 0.02), _vec(1, 0.015), _vec(1, 0.012), _vec(0, 1)]
    cl = im.cluster_facts(facts, vecs, 0.9)
    groups = sorted(sorted(c) for c in cl)
    assert groups == [[0, 1], [2], [3], [4]]          # разные type и полярность не склеиваются
    merged = {m["mentions"]: m for m in im.merge_clusters(facts, cl)}
    top = merged[2]
    assert top["fact"] == "обожает чай"               # представитель — самое свежее упоминание
    assert (top["first"], top["last"]) == (_date(2020, 1, 1), _date(2023, 1, 1))


def test_dedupe_self_falls_back_to_exact_without_embeddings():
    facts = [{"type": "likes", "fact": "Любит  чай", "date": _date(2020, 1, 1)},
             {"type": "likes", "fact": "любит чай", "date": _date(2022, 1, 1)},
             {"type": "likes", "fact": "любит кофе", "date": None}]
    out, how = im.dedupe_self(facts, 0.9, embed=lambda texts: [])
    assert how == "exact" and sorted(f["mentions"] for f in out) == [1, 2]


def test_self_facts_are_deduplicated_with_mention_counts_on_import(staging, monkeypatch):
    monkeypatch.setattr(im, "load_selfblock", lambda *a, **k: _sb())
    staging["rows"] = [row("Кай", "любит чай", about="kai", source_chat_id=11, source_message_date="2019-01-01", quote="a1"),
                       row("Кай", "обожает чай", about="kai", source_chat_id=12, source_message_date="2023-07-01", quote="a2"),
                       row("Кай", "любит кофе", about="kai", source_chat_id=13, source_message_date="2021-01-01", quote="a3")]
    emb = {"любит чай": [1, 0.01], "обожает чай": [1, 0.02], "любит кофе": [0, 1]}
    asyncio.run(im.run(False, True, 20, 0, 0.9, embed=lambda texts: [emb[t] for t in texts]))
    ppl, mem = _state()
    by = {m.fact_text: m for m in mem}
    assert set(by) == {"обожает чай", "любит кофе"}
    tea = by["обожает чай"]
    assert (tea.mention_count, tea.first_mention_date, tea.last_mention_date) == (2, _date(2019, 1, 1), _date(2023, 7, 1))
    assert tea.source_chat_id == 12 and tea.source_quote == "a2"


def test_related_to_uses_existing_person_name_when_candidate_name_is_empty(staging):
    """VK-диалог без названия даёт кандидата с пустым именем; у существующей карточки имя есть —
    факты не должны получать пустой related_to."""
    pid = None
    with get_engine().begin() as c:
        pid = c.execute(people.insert().values(user_id=MARK, name="Имя Из Api", aliases=["Алиас"], vk_id=77)
                        .returning(people.c.id)).scalar_one()
    staging["rows"] = [row("", "любит чай", source="vk", vk_id=77, source_chat_id=77)]
    _run()
    mem = _state()[1]
    assert len(mem) == 1 and mem[0].person_id == pid
    assert mem[0].related_to == "Имя Из Api | Алиас"
    assert not any(m.related_to.strip() == "" for m in mem)
