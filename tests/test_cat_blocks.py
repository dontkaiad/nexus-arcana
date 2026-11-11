"""tests/test_cat_blocks.py — блок факта про кота (profile | care | health | relations | owner | unsorted) и аудитория.

Реальная тестовая БД; подменена только граница LLM (ask_claude) и эмбеддинг-индексация. Имена вымышленные.
Главное свойство: закрыто по умолчанию — NULL, пустой, неизвестный блок и «сомнение» никогда не дают аудиторию «ситтер».
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import IntegrityError

from core import cat_blocks
from core import memory as mem
from core.cats import group_facts
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cat-blocks"
OTHER = "test-cat-blocks-other"
TG = 700000098
CATEGORY = "🐾 Коты"


@pytest.fixture(autouse=True)
def _cleanup():
    with patch("core.repos.pg_memory_repo._spawn_index"):
        yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        conn.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))
        conn.execute(delete(people).where(people.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def api():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


def _reply(fact, связь, block=None, category=CATEGORY, key="x"):
    d = {"fact": fact, "category": category, "связь": связь, "ключ": key}
    if block is not None:
        d["блок"] = block
    return json.dumps(d, ensure_ascii=False)


async def _store(message, reply, seen=None):
    llm = AsyncMock(return_value=reply)
    with patch.object(mem, "ask_claude", llm), patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"):
        out = await mem.parse_and_store(message, MARK, "☀️ Nexus")
    if seen is not None:
        seen.append(llm.await_args.kwargs["system"])
    return out


def _block(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT cat_block FROM memories WHERE id = :i"), {"i": int(mid)}).scalar_one()


def _mk_cat(name="Тучка"):
    return crepo.create(MARK, name)


def _mk_fact(text_, cat_id=None, user=MARK, **kw):
    vals = dict(fact_text=text_, key_name="k", category=CATEGORY, related_to="", user_id=user, review_status="confirmed", cat_id=cat_id)
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


# ── чистые функции ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [("care", "care"), ("  Care ", "care"), ("PROFILE", "profile"), ("owner", "owner"), ("unsorted", "unsorted"),
                                      ("", "unsorted"), (None, "unsorted"), (5, "unsorted"), (["care"], "unsorted"), ("care, owner", "unsorted"),
                                      ("уход", "unsorted"), ("sitter", "unsorted"), ("public", "unsorted")])
def test_normalize_accepts_only_exact_blocks_everything_else_is_unsorted(raw, want):
    assert cat_blocks.normalize(raw) == want


def test_audience_sitter_sees_profile_care_relations_and_critical_health_only():
    for b in ("profile", "care", "relations"):
        assert cat_blocks.audience(b) == "sitter" and cat_blocks.audience(b, "important") == "sitter"
    assert cat_blocks.audience("health", "important") == "sitter"
    for t in ("other", "likes", "dislikes", None):
        assert cat_blocks.audience("health", t) == "owner"
    for b in ("owner", "unsorted", None, "", "weird", "sitter", "Care"):
        for t in ("important", "other", None):
            assert cat_blocks.audience(b, t) == "owner"                  # по умолчанию закрыто, даже «критично» не открывает owner / unsorted / NULL


def test_every_declared_block_is_known_to_the_migration_and_the_prompt():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions" / "d3f5a7c9e124_memories_cat_block.py"
    spec = importlib.util.spec_from_file_location("mig_cat_block", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert tuple(mig.BLOCKS) == cat_blocks.BLOCKS
    for b in cat_blocks.BLOCKS:
        assert b in cat_blocks.prompt_block()


@pytest.mark.parametrize("text_,names,want", [("Тучка рыжая", ["Тучка"], True), ("тучку помыли", ["Тучка"], True), ("кот боится пылесоса", [], True),
                                              ("питомец спит", [], True), ("royal canin indoor 2кг", ["Тучка"], False), ("купила молоко", [], False), ("", None, False)])
def test_wants_block_is_a_cheap_gate(text_, names, want):
    assert cat_blocks.wants_block(text_, names) is want


async def test_prompt_block_is_added_to_the_same_call_only_when_asked():
    seen = []
    await _store("кот боится пылесоса", _reply("боится пылесоса", "кот", "care"), seen)
    assert "БЛОК ФАКТА ПРО КОТА" in seen[0]
    await _store("купила молоко", _reply("купила молоко", "", None, category="🏠 Быт"), seen)
    assert "БЛОК ФАКТА ПРО КОТА" not in seen[1]


async def test_one_llm_call_per_message_and_no_extra_cost_without_cat_words():
    llm = AsyncMock(return_value=_reply("купила молоко", "", None, category="🏠 Быт"))
    with patch.object(mem, "ask_claude", llm), patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), patch("core.repos.pg_memory_repo._spawn_index"):
        await mem.parse_and_store("купила молоко", MARK, "☀️ Nexus")
    assert llm.await_count == 1 and llm.await_args.kwargs["max_tokens"] == 200
    llm = AsyncMock(return_value=_reply("боится пылесоса", "кот", "care"))
    with patch.object(mem, "ask_claude", llm), patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), patch("core.repos.pg_memory_repo._spawn_index"):
        await mem.parse_and_store("кот боится пылесоса", MARK, "☀️ Nexus")
    assert llm.await_count == 1 and llm.await_args.kwargs["max_tokens"] == 240


# ── запись ──────────────────────────────────────────────────────────────────

async def test_general_cat_fact_gets_the_block_from_the_same_call():
    out = await _store("кот боится пылесоса", _reply("боится пылесоса", "кот", "care"))
    assert _block(out["memory_id"]) == "care"


async def test_cat_fact_bound_to_a_card_gets_the_block():
    _mk_cat("Тучка")
    out = await _store("Тучка не ест курицу", _reply("Тучка не ест курицу", "тучка", "care"))
    assert out["cat_id"] and _block(out["memory_id"]) == "care"


@pytest.mark.parametrize("answer", [None, "", "Уход", "sitter", "care owner", 7, "unsorted"])
async def test_missing_or_bad_answer_is_unsorted_not_open_and_not_null(answer):
    d = {"fact": "боится пылесоса", "category": CATEGORY, "связь": "кот", "ключ": "x"}
    if answer is not None:
        d["блок"] = answer
    out = await _store("кот боится пылесоса", json.dumps(d, ensure_ascii=False))
    assert _block(out["memory_id"]) == "unsorted"


async def test_gate_off_the_model_answer_is_ignored_and_the_fact_is_unsorted():
    # «royal canin» без слова про кота: блок в промпт не просили, а модель всё равно ответила «care» — доверять нечему
    out = await _store("royal canin indoor 2кг", _reply("royal canin indoor 2кг", "коты", "care"))
    assert _block(out["memory_id"]) == "unsorted"


async def test_non_cat_facts_have_no_block():
    out = await _store("купила молоко", _reply("купила молоко", "", "care", category="🏠 Быт"))
    assert _block(out["memory_id"]) is None


async def test_repeat_of_the_same_fact_does_not_override_a_block_that_is_already_set():
    _mk_cat("Тучка")
    first = await _store("Тучка не ест курицу", _reply("Тучка не ест курицу", "тучка", "unsorted"))
    mid = first["memory_id"]
    again = await _store("Тучка не ест курицу", _reply("Тучка не ест курицу", "тучка", "care"))
    assert again["memory_id"] == mid and _block(mid) == "care"                   # unsorted → можно уточнить
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET cat_block = 'owner' WHERE id = :i"), {"i": int(mid)})
    await _store("Тучка не ест курицу", _reply("Тучка не ест курицу", "тучка", "care"))
    assert _block(mid) == "owner"                                                # разметка владельца не перетирается повтором


def test_database_refuses_an_unknown_block():
    with pytest.raises(IntegrityError):
        _mk_fact("x", cat_block="sitter")
    mid = _mk_fact("y", cat_block="care")
    assert _block(mid) == "care"


# ── владелец ставит блок ─────────────────────────────────────────────────────

def test_owner_sets_a_block_on_a_cat_fact_and_on_a_general_fact(api):
    cid = _mk_cat()
    a, g = _mk_fact("Тучка не ест курицу", cid), _mk_fact("кот боится пылесоса")
    for mid in (a, g):
        r = api.patch(f"/api/cats/facts/{mid}/block", json={"block": "care"})
        assert r.status_code == 200 and r.json() == {"ok": True, "fact_id": mid, "block": "care"}
        assert _block(mid) == "care"
    assert api.patch(f"/api/cats/facts/{a}/block", json={"block": "owner"}).status_code == 200 and _block(a) == "owner"


@pytest.mark.parametrize("bad", ["sitter", "", "Care", "care ", None, 5])
def test_unknown_block_is_422_and_nothing_changes(api, bad):
    mid = _mk_fact("кот боится пылесоса", cat_block="owner")
    assert api.patch(f"/api/cats/facts/{mid}/block", json={"block": bad}).status_code == 422
    assert _block(mid) == "owner"


def test_foreign_person_archived_rejected_empty_and_non_cat_facts_are_404(api):
    cid = _mk_cat()
    with get_engine().begin() as c:
        pid = c.execute(insert(people).values(user_id=MARK, name="Оля").returning(people.c.id)).scalar_one()
    cases = {
        "foreign": _mk_fact("чужой", user=OTHER),
        "person": _mk_fact("факт карточки человека", person_id=pid),
        "archived": _mk_fact("архив", cid, is_archived=True),
        "rejected": _mk_fact("отклонён", cid, review_status="rejected"),
        "empty": _mk_fact("", cid),
        "other-category": _mk_fact("не про котов", category="🏠 Быт"),
    }
    for name, mid in cases.items():
        assert api.patch(f"/api/cats/facts/{mid}/block", json={"block": "care"}).status_code == 404, name
        assert _block(mid) is None, name
    assert api.patch("/api/cats/facts/999999999/block", json={"block": "care"}).status_code == 404


def test_manual_fact_from_the_app_is_unsorted_by_default_and_takes_an_explicit_block(api):
    cid = _mk_cat()
    r = api.post(f"/api/cats/{cid}/facts", json={"text": "любит окно"})
    assert _block(r.json()["fact_id"]) == "unsorted"
    r = api.post(f"/api/cats/{cid}/facts", json={"text": "спит на диване", "block": "care"})
    assert _block(r.json()["fact_id"]) == "care"
    r = api.post(f"/api/cats/{cid}/facts", json={"text": "боится гостей", "block": "sitter"})
    assert _block(r.json()["fact_id"]) == "unsorted"                              # неизвестное значение не открывает факт


def test_blocks_are_visible_to_the_owner_in_card_and_general_lists_and_groups(api):
    cid = _mk_cat()
    f = _mk_fact("Тучка рыжая", cid, cat_block="profile", key_name="cat_1_other_aaaaaaaa")
    _mk_fact("кот боится пылесоса", cat_block="care")
    facts = crepo.list_facts(MARK, cid)
    assert facts[0]["block"] == "profile"
    assert group_facts(facts, "Тучка", with_block=True)[0]["items"][0]["block"] == "profile"
    assert "block" not in group_facts(facts, "Тучка")[0]["items"][0]                    # по умолчанию (контракт читающего токена) блока нет
    card = api.get(f"/api/cats/{cid}").json()
    assert card["facts"][0]["items"][0]["block"] == "profile"                           # вход владельца видит блок
    assert api.get("/api/cats?with_facts=1").json()["cats"][0]["facts"][0]["items"][0]["block"] == "profile"
    assert api.get("/api/cats/general").json()["facts"][0]["items"][0]["block"] == "care"
    assert [x["block"] for x in crepo.list_general_facts(MARK)] == ["care"]
    assert f
