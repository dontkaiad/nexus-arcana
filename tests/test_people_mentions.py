"""tests/test_people_mentions.py — резолв упоминаний людей в контексте памяти (ADR-0027, PR-3).
Реальная тестовая БД; LLM/сеть не используются. Privacy: generic имена."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, text

from core import memory as mem
from core.db import get_engine
from core.people import aliases_norm_for, birthday_context, mention_candidates, norm_forms
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests._people_helpers import insert_person
from tests.test_people_api import mk_fact

MARK = "test-people-mentions"
OTHER = "test-people-mentions-other"


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        for u in (MARK, OTHER):
            c.execute(delete(memories).where(memories.c.user_id == u))
            c.execute(delete(people).where(people.c.user_id == u))


def mk(name, aliases=(), user=MARK, **kw):
    pid = insert_person(user, name, aliases=list(aliases), **kw)
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)
    return pid


def ids(mentions, user=MARK):
    return [p["id"] for p in prepo.resolve(user, mentions)]


async def ctx(*kw):
    with patch("core.memory_search._vector_leg", return_value=[]):
        return await mem.get_memories_for_context(MARK, list(kw), bot_label="☀️ Nexus")


# ── нормализация ─────────────────────────────────────────────────────────────

def test_norm_declensions_regular_name():
    a = set(aliases_norm_for("Маша Иванова", []))
    for m in ("Маше", "с Машей", "Машу", "Маша", "Ивановой"):
        assert a & set(mention_candidates([m])), m


def test_norm_short_name_declensions_exact_only():
    a = set(aliases_norm_for("Оля Петрова", []))
    for m in ("Оле", "с Олей", "Олю", "Оли", "Оля"):
        assert a & set(mention_candidates([m])), m
    for m in ("Олег", "Олеся", "Олесю", "Ол"):
        assert not (a & set(mention_candidates([m]))), m


def test_birthday_context_year_only_if_known():
    assert birthday_context(date(1990, 3, 5), True) == "ДР: 05.03.1990"
    assert birthday_context(date(1904, 3, 5), False) == "ДР: 05.03"
    assert birthday_context(date(1990, 3, 5), False) == "ДР: 05.03"
    assert birthday_context(None) == ""


# ── resolve ──────────────────────────────────────────────────────────────────

def test_resolve_cases_and_other_names():
    pid = mk("Оля Петрова", aliases=["Лёля"])
    assert ids(["Оле"]) == [pid] and ids(["с Олей"]) == [pid]
    assert ids(["Лёле"]) == [pid]                          # «Другие имена» тоже резолвятся
    assert ids(["Олег"]) == [] and ids(["Олеся"]) == []


def test_resolve_excludes_rejected_self_and_foreign():
    mk("Маша Один", status="rejected")
    mk("Даша Два", is_self=True)
    mk("Катя Три", user=OTHER)
    assert ids(["Маше"]) == [] and ids(["Даше"]) == [] and ids(["Кате"]) == []


def test_resolve_homonyms_returns_all():
    a, b = mk("Маша Один"), mk("Маша Два")
    assert ids(["Маше"]) == [a, b]
    assert ids(["Маше Два"]) == [a, b][1:] or set(ids(["Маше Два"])) >= {b}


def test_resolve_after_patch_and_merge_keeps_norm_in_sync():
    pid = mk("Маша Один")
    prepo.update_person(MARK, pid, {"aliases": ["Зайка"]})
    assert ids(["Зайке"]) == [pid]
    prepo.update_person(MARK, pid, {"name": "Нина Один"})
    assert ids(["Нине"]) == [pid] and ids(["Маше"]) == []
    src = mk("Вера Пять", aliases=["Верунчик"])
    prepo.merge_people(MARK, src, pid)
    assert ids(["Вере"]) == [pid] and ids(["Верунчику"]) == [pid]


def test_migration_backfill_matches_runtime_forms():
    """aliases_norm строится одной функцией — и в миграции, и в репозитории."""
    pid = mk("Оля Петрова", aliases=["Лёля"])
    with get_engine().connect() as c:
        got = c.execute(text("SELECT aliases_norm FROM people WHERE id=:i"), {"i": pid}).scalar()
    assert got == aliases_norm_for("Оля Петрова", ["Лёля"])


# ── контекст ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_one_person_confirmed_important_first_with_birthday():
    pid = mk("Оля Петрова", birthday=date(1904, 3, 5), birthday_year_known=False)
    mk_fact(pid, "likes", "confirmed", "Оля: любит чай", user=MARK)
    mk_fact(pid, "important", "confirmed", "Оля: аллергия на орехи", user=MARK)
    mk_fact(pid, "other", "pending", "Оля: не проверено", user=MARK)
    mk_fact(pid, "other", "rejected", "Оля: отклонено", user=MARK)
    out = await ctx("Оле")
    assert "ДР: 05.03" in out and "05.03.1904" not in out
    assert out.index("аллергия на орехи") < out.index("любит чай")
    assert "не проверено" not in out and "отклонено" not in out
    assert "[" not in out.split("Из «Людей»")[1]            # один человек — без пометки


@pytest.mark.asyncio
async def test_context_homonyms_both_sets_labeled():
    a, b = mk("Маша Один"), mk("Маша Два")
    mk_fact(a, "likes", "confirmed", "Маша: факт-а", user=MARK)
    mk_fact(b, "likes", "confirmed", "Маша: факт-б", user=MARK)
    out = await ctx("Маше")
    assert "[Маша Один]" in out and "[Маша Два]" in out and "факт-а" in out and "факт-б" in out
    assert "неясно, о ком речь" in out


@pytest.mark.asyncio
async def test_context_rejected_and_erased_not_included():
    rej = mk("Нина Один", status="rejected")
    mk_fact(rej, "likes", "rejected", "Нина: любит джаз", user=MARK)   # «не мой чел» отклоняет и факты
    ok = mk("Нина Два")
    mk_fact(ok, "likes", "confirmed", "Нина: любит рок", user=MARK)
    fid = mk_fact(ok, "other", "rejected", "Нина: секрет", user=MARK)
    assert prepo.erase_fact(MARK, ok, fid) == "ok"           # tombstone: пустой текст, rejected
    out = await ctx("Нине")
    assert "любит рок" in out and "любит джаз" not in out and "секрет" not in out


@pytest.mark.asyncio
async def test_context_dedupes_with_regular_search():
    pid = mk("Маша Один")
    mk_fact(pid, "likes", "confirmed", "Маша: уникальный-факт-дедуп", user=MARK)
    out = await ctx("Маша", "Маше", "Машей")
    assert out.count("уникальный-факт-дедуп") == 1


@pytest.mark.asyncio
async def test_context_no_person_regular_search_unchanged():
    out = await ctx("слово-которого-нет")
    assert out == ""
    assert await mem.get_memories_for_context(MARK, []) == ""


# ── факты отклонённых людей скрыты во всех путях чтения ─────────────────────

@pytest.mark.asyncio
async def test_facts_of_rejected_person_hidden_in_all_search_paths():
    from core.repos.pg_memory_repo import PgMemoryRepo
    rej = mk("Зоя Один", status="rejected")
    ok = mk("Зоя Два")
    f_rej = mk_fact(rej, "likes", "confirmed", "ЗоЯ-токен отклонённый", user=MARK)     # confirmed, но человек отклонён
    f_ok = mk_fact(ok, "likes", "confirmed", "ЗоЯ-токен нормальный", user=MARK)
    repo = PgMemoryRepo()
    got = {m.id for m in await repo.search(["ЗоЯ-токен"], user_id=MARK)}
    assert str(f_ok) in got and str(f_rej) not in got
    got = {m.id for m in await repo.find_recent(user_id=MARK, page_size=50)}
    assert str(f_ok) in got and str(f_rej) not in got
    got = {m.id for m in await repo.find_by_category("👥 Люди", user_id=MARK)}
    assert str(f_ok) in got and str(f_rej) not in got
    # деструктивные пути видят всё (confirmed_only=False)
    got = {m.id for m in await repo.search(["ЗоЯ-токен"], user_id=MARK, confirmed_only=False)}
    assert {str(f_ok), str(f_rej)} <= got


def test_semantic_search_hides_rejected_person(monkeypatch):
    from core import memory_rag
    vec = [1.0] + [0.0] * 1023
    monkeypatch.setattr(memory_rag, "_embed", lambda *a, **k: [vec])
    rej = mk("Зоя Один", status="rejected")
    f = mk_fact(rej, "likes", "confirmed", "семантика отклонённого", user=MARK)
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET embedding = CAST(:e AS vector) WHERE id = :i"),
                  {"e": memory_rag._vec_literal(vec), "i": f})
    assert str(f) not in {str(m.id) for m in memory_rag.search_memory_semantic("что угодно", user_id=MARK)}
