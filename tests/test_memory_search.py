"""tests/test_memory_search.py — единый гибридный поиск по 🧠 Память (core/memory_search.py).

Юниты (без БД): fts_query, RRF, парсинг/фолбэк реранка.
Интеграция (реальный Postgres из DATABASE_URL, нужна миграция memories.fts):
фильтры confirmed / pending / rejected-человек / TOMBSTONE («Стереть»: fact_text='' +
review_status='rejected'), склонения имён в FTS, scope/user_id, вектор-нога.

Privacy: generic фикстуры. Все строки помечаются user_id=MARK и удаляются.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, insert

from core import memory_search as ms
from core.db import get_engine
from core.repos.memories_table import memories
from core.repos.people_tables import people
from core.repos.pg_memory_repo import Memory
from tests._people_helpers import insert_person

MARK = "test-memsearch"
CAT = "🛒 Предпочтения"


# ── юниты ────────────────────────────────────────────────────────────────────

def test_fts_query_drops_stopwords_and_joins_with_or():
    assert ms.fts_query("что я не ем") == "ем"
    assert ms.fts_query("когда у Оли др") == "оли | др"
    assert ms.fts_query("номер квартиры") == "номер | квартиры"


def test_fts_query_is_injection_safe():
    q = ms.fts_query("a' | b:* & !(c) ; drop table")
    assert set(q.replace(" | ", " ").split()) <= {"drop", "table"}
    for ch in "'&!():*;":
        assert ch not in q


def test_fts_query_empty():
    assert ms.fts_query("") == "" and ms.fts_query("что и не") == ""


def test_rrf_fuse_orders_by_combined_rank():
    hits = ms.rrf_fuse([10, 20, 30], [(20, 0.9), (40, 0.8), (10, 0.7)])
    order = [h.memory_id for h in hits]
    assert order[:2] == [20, 10]                 # есть в обеих ногах — выше одиночных
    by = {h.memory_id: h for h in hits}
    assert by[20].fts_rank == 2 and by[20].vec_rank == 1 and by[20].vec_score == 0.9
    assert by[40].fts_rank is None and by[30].vec_rank is None


def _hit(i, fact="факт"):
    return ms.Hit(memory_id=i, score=1.0 / i, memory=Memory(id=str(i), fact=fact))


def test_rerank_orders_and_caps():
    hits = [_hit(1), _hit(2), _hit(3)]
    with patch.object(ms, "ask_claude", AsyncMock(return_value="[3, 1]")):
        out = asyncio.run(ms.rerank("q", hits, 5))
    assert [h.memory.id for h in out] == ["3", "1"] and all(h.reranked for h in out)


def test_rerank_caps_to_top_n_and_ignores_unknown_ids():
    hits = [_hit(1), _hit(2), _hit(3)]
    with patch.object(ms, "ask_claude", AsyncMock(return_value='["9","2","1","3"]')):
        out = asyncio.run(ms.rerank("q", hits, 2))
    assert [h.memory.id for h in out] == ["2", "1"]


def test_rerank_valid_empty_list_means_nothing_relevant():
    with patch.object(ms, "ask_claude", AsyncMock(return_value="[]")):
        assert asyncio.run(ms.rerank("q", [_hit(1)], 5)) == []


@pytest.mark.parametrize("raw", ["", "не JSON", '{"ids": [1]}'])
def test_rerank_bad_answer_falls_back_to_rrf_order(raw):
    hits = [_hit(1), _hit(2), _hit(3)]
    with patch.object(ms, "ask_claude", AsyncMock(return_value=raw)):
        out = asyncio.run(ms.rerank("q", hits, 2))
    assert [h.memory.id for h in out] == ["1", "2"]


def test_rerank_markdown_fenced_json():
    with patch.object(ms, "ask_claude", AsyncMock(return_value='```json\n["2"]\n```')):
        out = asyncio.run(ms.rerank("q", [_hit(1), _hit(2)], 5))
    assert [h.memory.id for h in out] == ["2"]


def test_rerank_prompt_has_person_name_and_category():
    m = Memory(id="1", fact="любит чай", related_to="Оля | Олечка", category="👥 Люди", person_id=5)
    ask = AsyncMock(return_value="[]")
    with patch.object(ms, "ask_claude", ask):
        asyncio.run(ms.rerank("q", [ms.Hit(memory_id=1, score=1, memory=m)], 5))
    prompt = ask.await_args.kwargs["prompt"]
    assert "Оля: любит чай" in prompt and "👥 Люди" in prompt


def test_empty_query_returns_nothing_without_calls():
    with patch.object(ms, "_fts_leg") as f, patch.object(ms, "_vector_leg") as v:
        assert asyncio.run(ms.hybrid_search("   ")) == []
    f.assert_not_called(); v.assert_not_called()


def test_hybrid_skips_rerank_when_disabled_and_traces():
    legs = []
    with patch.object(ms, "_fts_leg", return_value=[1, 2]), \
         patch.object(ms, "_vector_leg", return_value=[(2, 0.9), (3, 0.5)]), \
         patch.object(ms, "_load", return_value={i: Memory(id=str(i), fact="f") for i in (1, 2, 3)}), \
         patch.object(ms, "rerank", AsyncMock()) as rr:
        out = asyncio.run(ms.hybrid_search("q", k=2, do_rerank=False,
                                           trace=lambda step, d: legs.append(step)))
    rr.assert_not_called()
    assert [h.memory.id for h in out] == ["2", "1"]
    assert legs == ["legs", "rrf"]


def test_hybrid_survives_vector_leg_failure_fts_only():
    with patch.object(ms, "_fts_leg", return_value=[7]), \
         patch.object(ms, "_vector_leg", return_value=[]), \
         patch.object(ms, "_load", return_value={7: Memory(id="7", fact="f")}):
        out = asyncio.run(ms.hybrid_search("q", do_rerank=False))
    assert [h.memory.id for h in out] == ["7"]


# ── интеграция с реальным Postgres ──────────────────────────────────────────

@pytest.fixture
def db():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


@pytest.fixture(autouse=True)
def _no_voyage():
    """Вектор-ногу по умолчанию выключаем (нет сети/ключа); где нужна — патчим явно."""
    with patch.object(ms, "_embed", return_value=[]):
        yield


def _ins(fact, status="confirmed", user=MARK, person_id=None, related="", scope="global",
         category=CAT, key=""):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=fact, key_name=key, category=category, related_to=related, user_id=user,
            review_status=status, person_id=person_id, scope=scope,
        ).returning(memories.c.id)).scalar_one()


def _search(q, **kw):
    kw.setdefault("user_id", MARK)
    kw.setdefault("do_rerank", False)
    return asyncio.run(ms.hybrid_search(q, **kw))


def _ids(hits):
    return {h.memory.id for h in hits}


def test_fts_finds_by_word_form(db):
    a = _ins("Оля родилась пятого мая")
    _ins("совсем другой факт про чай")
    assert _ids(_search("когда у Оли др")) == {str(a)}     # Оли → Оля (stem 'ол')


def test_fts_ranks_fact_text_above_related_to(db):
    in_related = _ins("любит чай", related="Зюзябрикс")
    in_fact = _ins("зюзябрикс любит чай")
    hits = _search("зюзябрикс")
    assert [h.memory.id for h in hits] == [str(in_fact), str(in_related)]


def test_key_name_is_not_searched(db):
    _ins("совсем другое", key="зюзябрикс_ключ")
    assert _search("зюзябрикс_ключ") == []


def test_pending_and_rejected_status_hidden(db):
    ok = _ins("зюзябрикс подтверждён")
    _ins("зюзябрикс ожидает", status="pending")
    _ins("зюзябрикс отклонён", status="rejected")
    assert _ids(_search("зюзябрикс")) == {str(ok)}


def test_facts_of_rejected_person_hidden(db):
    pid_ok = insert_person(MARK, "Тестовая Хорошая")
    pid_bad = insert_person(MARK, "Тестовая Чужая", status="rejected")
    ok = _ins("зюзябрикс факт", person_id=pid_ok)
    _ins("зюзябрикс факт чужой", person_id=pid_bad)       # сам факт confirmed
    assert _ids(_search("зюзябрикс")) == {str(ok)}


def test_tombstone_never_returned_fts_and_vector(db):
    """«Стереть» (erase_fact): fact_text='', related_to='', embedding=NULL, review_status='rejected',
    ключ остаётся. Даже если статус вручную вернули в confirmed — пустой факт в поиск не попадает."""
    pid = insert_person(MARK, "Тестовая Стёртая")
    tomb = _ins("", status="rejected", person_id=pid, key="person_dislikes_x")
    tomb_confirmed = _ins("", status="confirmed", person_id=pid, key="person_dislikes_y")
    ok = _ins("зюзябрикс живой")
    vec = [1.0] + [0.0] * 1023
    with get_engine().begin() as c:                        # вектор-нога без Voyage: кладём embedding руками
        for mid in (tomb, tomb_confirmed, ok):
            c.execute(sa.text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"),
                      {"e": ms._vec_literal(vec), "i": mid})
    with patch.object(ms, "_embed", return_value=[vec]):
        got = _ids(_search("что угодно зюзябрикс"))
    assert got == {str(ok)}


def test_vector_leg_respects_all_filters(db):
    ok = _ins("факт один")
    pend = _ins("факт два", status="pending")
    pid_bad = insert_person(MARK, "Тестовая Чужая", status="rejected")
    bad = _ins("факт три", person_id=pid_bad)
    archived = _ins("факт четыре")
    other_user = _ins("факт пять", user=MARK + "-other")
    vec = [1.0] + [0.0] * 1023
    with get_engine().begin() as c:
        c.execute(sa.text("UPDATE memories SET is_archived = true WHERE id = :i"), {"i": archived})
        for mid in (ok, pend, bad, archived, other_user):
            c.execute(sa.text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"),
                      {"e": ms._vec_literal(vec), "i": mid})
    try:
        with patch.object(ms, "_embed", return_value=[vec]):
            got = _ids(_search("запрос без слов совпадения"))
        assert got == {str(ok)}
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == MARK + "-other"))


def test_inactive_not_returned(db):
    cur = _ins("зюзябрикс текущий")
    old = _ins("зюзябрикс старый")
    with get_engine().begin() as c:
        c.execute(sa.text("UPDATE memories SET is_current = false WHERE id = :i"), {"i": old})
    assert _ids(_search("зюзябрикс")) == {str(cur)}


def test_scope_filter_includes_global(db):
    g = _ins("зюзябрикс глобальный", scope="global")
    a = _ins("зюзябрикс аркана", scope="arcana")
    n = _ins("зюзябрикс нексус", scope="nexus")
    assert _ids(_search("зюзябрикс", scope="arcana")) == {str(g), str(a)}
    assert _ids(_search("зюзябрикс")) == {str(g), str(a), str(n)}


def test_person_ids_filter_hook(db):
    p1 = insert_person(MARK, "Тестовая Первая")
    p2 = insert_person(MARK, "Тестовая Вторая")
    f1 = _ins("зюзябрикс первая", person_id=p1)
    _ins("зюзябрикс вторая", person_id=p2)
    assert _ids(_search("зюзябрикс", person_ids=[p1])) == {str(f1)}


def test_user_isolation(db):
    _ins("зюзябрикс чужой", user=MARK + "-other")
    try:
        assert _search("зюзябрикс") == []
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == MARK + "-other"))


def test_generated_fts_column_updates_on_edit(db):
    mid = _ins("старый текст")
    with get_engine().begin() as c:
        c.execute(sa.text("UPDATE memories SET fact_text = 'зюзябрикс новый' WHERE id = :i"), {"i": mid})
    assert _ids(_search("зюзябрикс")) == {str(mid)}
    assert _search("старый") == []
