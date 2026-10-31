"""tests/test_memory_answer.py — этап 2: answer() — понимание вопроса → прямой запрос / гибрид.

Реальный Postgres (DATABASE_URL, миграция memories.fts). Voyage выключен, Haiku замокан.
Проверяем: тёзки → два блока, тип из ключа, пустой прямой результат = «не знаю» (без гибрида),
pending/rejected/tombstone/стёртые не попадают, «я» → только мои факты, ДР из карточки, флаг отката.
Privacy: вымышленные имена и факты, всё помечено user_id=MARK и чистится.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, insert

from core import memory_search as ms
from core.db import get_engine
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests._people_helpers import insert_person

MARK = "test-answer"
CAT = "👥 Люди"


@pytest.fixture(autouse=True)
def _clean():
    with patch.object(ms, "_embed", return_value=[]):      # вектор-ноги нет
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def _fact(pid, typ, text, status="confirmed", n=[0], **kw):
    n[0] += 1
    key = "person_%d_%s_%08x" % (pid, typ, n[0])
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text, key_name=key, category=CAT, person_id=pid, user_id=MARK,
            review_status=status, related_to=kw.pop("related_to", ""), **kw).returning(memories.c.id)).scalar_one()


def _own(text, status="confirmed", cat="🛒 Предпочтения"):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text, key_name="", category=cat, user_id=MARK, review_status=status,
        ).returning(memories.c.id)).scalar_one()


def run(q, **kw):
    kw.setdefault("do_rerank", False)
    return asyncio.run(ms.answer(q, user_id=MARK, **kw))


def mk(name, aliases=(), **cols):
    return insert_person(MARK, name, aliases=list(aliases), status="confirmed", **cols)


def _sync_norm(pid):
    from core.repos import pg_people_repo as prepo
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)


def person(name, aliases=(), **cols):
    pid = mk(name, aliases, **cols)
    _sync_norm(pid)
    return pid


def memids(res):
    return {m.id for m in res.memories}


# ── прямой запрос по человеку и типу ─────────────────────────────────────────

def test_direct_returns_only_requested_types_in_importance_order():
    p = person("Ольга", ["Оля"])
    dl = _fact(p, "dislikes", "не ест грибы")
    im = _fact(p, "important", "аллергия на орехи")
    lk = _fact(p, "likes", "любит чай")
    res = run("что не любит оля")
    assert res.route == "direct" and res.kind == "facts" and res.subject == "person"
    assert [h.memory.id for h in res.hits] == [str(im), str(dl)]       # important первым, likes нет
    assert str(lk) not in memids(res)
    assert run("что любит оле").hits[0].memory.id == str(lk)


def test_direct_empty_is_none_not_similar_facts():
    p = person("Ольга", ["Оля"])
    _fact(p, "likes", "любит чай")
    with patch.object(ms, "hybrid_search", AsyncMock()) as hyb:
        res = run("что не любит оля")
    hyb.assert_not_called()                      # никакого гибрида и «похожих» фактов
    assert res.route == "direct" and res.kind == "none" and res.hits == []


def test_pending_rejected_tombstone_never_in_direct():
    p = person("Ольга", ["Оля"])
    ok = _fact(p, "dislikes", "не любит шум")
    _fact(p, "dislikes", "не любит ожидание", status="pending")
    _fact(p, "dislikes", "не любит отклонённое", status="rejected")
    _fact(p, "dislikes", "", status="rejected")                         # tombstone
    _fact(p, "dislikes", "", status="confirmed")                        # пустой текст при confirmed
    assert memids(run("что не любит оля")) == {str(ok)}


def test_rejected_person_not_resolved_so_unknown():
    bad = insert_person(MARK, "Ольга", aliases=["Оля"], status="rejected")
    _sync_norm(bad)
    _fact(bad, "dislikes", "не любит шум")
    res = run("что не любит оля")
    assert res.hits == [] and res.route in ("unknown_person", "hybrid")


# ── тёзки ────────────────────────────────────────────────────────────────────

def test_homonyms_give_two_labeled_groups_with_notes():
    a = person("Михаил Тестов", ["Миша"], notes="коллега")
    b = person("Миша Образцов", ["Миша"])
    fa = _fact(a, "dislikes", "не любит опозданий")
    fb = _fact(b, "dislikes", "не любит жару")
    res = run("что не любит миша")
    assert res.subject == "ambiguous" and len(res.groups) == 2
    by = {g.person_id: g for g in res.groups}
    assert by[a].label == "Михаил Тестов (коллега)" and by[b].label == "Миша Образцов"
    assert [h.memory.id for h in by[a].hits] == [str(fa)] and [h.memory.id for h in by[b].hits] == [str(fb)]


def test_diminutive_does_not_match_misha():
    a = person("Михаил Тестов", ["Миша"])
    other = person("Мишаня")
    _fact(a, "likes", "любит джаз")
    _fact(other, "likes", "любит рок")
    res = run("что любит миша")
    assert [g.person_id for g in res.groups] == [a]


# ── неизвестное имя / ДР ─────────────────────────────────────────────────────

def test_unknown_person():
    res = run("что не любит ваня")
    assert res.route == "unknown_person" and res.kind == "unknown_person" and res.unknown_name == "ваня"


def test_unknown_word_present_in_facts_goes_to_hybrid_not_unknown_person():
    _own("кофе по утрам")
    assert run("что любит кофе").route != "unknown_person"


def test_birthday_from_card_minimal():
    p = person("Ольга", ["Оля"], birthday=date(1990, 3, 5), birthday_year_known=True)
    res = run("когда у оли др")
    assert res.route == "birthday" and res.kind == "birthday"
    assert "05.03" in res.groups[0].birthday and res.groups[0].person_id == p


def test_birthday_missing_is_none():
    person("Ольга", ["Оля"])
    res = run("когда у оли др")
    assert res.route == "birthday" and res.kind == "none"


# ── «я»: только мои факты ────────────────────────────────────────────────────

def test_self_subject_restricts_hybrid_to_own_facts():
    p = person("Ольга", ["Оля"])
    mine = _own("не ем сладкое")
    _fact(p, "dislikes", "не ест сладкое")               # чужой, но похожий по словам
    res = run("что я не ем")
    assert res.route == "hybrid_self" and res.subject == "self"
    assert memids(res) <= {str(mine)}
    assert str(mine) in memids(res) or res.hits == []   # FTS «ем» может не поймать; чужого — никогда


def test_self_search_ignores_pending_self_card_facts():
    me = insert_person(MARK, "Я", status="confirmed", is_self=True)
    _fact(me, "dislikes", "не ем рыбу", status="pending")
    res = run("что я не ем")
    assert res.hits == []


def test_self_hybrid_gets_strict_rerank_hint():
    seen = {}

    async def fake_ask(**kw):
        seen.update(kw)
        return "[]"

    _own("не ем сладкое")
    with patch.object(ms, "ask_claude", fake_ask):
        res = run("что я не ем сладкое", do_rerank=True)
    assert res.kind == "none"                            # реранк сказал [] → «не знаю»
    assert "субъект=self" in seen["prompt"] and "отрицание" in seen["prompt"]
    assert "Отрицание в запросе и утверждение в факте" in seen["system"]
    assert "пустой ответ лучше неверного" in seen["system"]


# ── откат и прочее ───────────────────────────────────────────────────────────

def test_flag_off_returns_stage1_hybrid(monkeypatch):
    monkeypatch.setenv("MEMORY_QUERY_UNDERSTANDING", "0")
    p = person("Ольга", ["Оля"])
    _fact(p, "dislikes", "не любит шум")
    with patch.object(ms, "hybrid_search", AsyncMock(return_value=[])) as hyb:
        res = run("что не любит оля")
    hyb.assert_awaited_once()
    assert res.route == "hybrid"


def test_person_without_type_uses_hybrid_scoped_to_person():
    p = person("Ольга", ["Оля"])
    f = _fact(p, "other", "была на даче летом")
    _own("дача у родителей")
    res = run("что оля говорила про дачу")
    assert res.route == "hybrid_person" and memids(res) == {str(f)}


def test_empty_query():
    assert run("  ").kind == "none"
