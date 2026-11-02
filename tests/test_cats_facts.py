"""tests/test_cats_facts.py — Cats phase 2, step 1: ключ и тип факта кота, upsert без дублей.

Реальный путь сообщения (подменена только граница LLM), имена вымышленные.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, text

from core import cats as cats_core
from core import memory as mem
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories

MARK = "test-cats-p2-facts"
CATEGORY = "🐾 Коты"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id == MARK))
        conn.execute(delete(cats).where(cats.c.user_id == MARK))


def _haiku(fact, связь, key="x"):
    return json.dumps({"fact": fact, "category": CATEGORY, "связь": связь, "ключ": key}, ensure_ascii=False)


async def _store(message, reply):
    with patch.object(mem, "ask_claude", AsyncMock(return_value=reply)), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"):
        return await mem.parse_and_store(message, MARK, "☀️ Nexus")


def _rows(cat_id):
    with get_engine().connect() as c:
        return c.execute(text("SELECT id, key_name, fact_text, related_to FROM memories "
                              "WHERE cat_id = :c ORDER BY id"), {"c": cat_id}).all()


# ── чистые функции ──────────────────────────────────────────────────────────

def test_key_format_and_type_roundtrip():
    key = cats_core.cat_fact_key(7, "dislikes", "Тучка не ест курицу")
    assert key.startswith("cat_7_dislikes_") and len(key.split("_")[-1]) == 8
    assert cats_core.cat_key_type(key) == "dislikes"
    assert cats_core.cat_fact_key(7, "dislikes", "  тучка   НЕ ест курицу ") == key      # нормализация как у людей
    assert cats_core.cat_fact_key(7, "weird", "x").startswith("cat_7_other_")


def test_retype_key_and_foreign_keys():
    key = cats_core.cat_fact_key(3, "other", "любит окно")
    assert cats_core.retype_cat_key(key, "likes").startswith("cat_3_likes_")
    assert cats_core.retype_cat_key(key, "nope") is None
    assert cats_core.retype_cat_key("person_3_other_deadbeef", "likes") is None       # ключ человека не подходит
    assert cats_core.cat_key_type("тучка_еда") is None


def test_fact_kind_falls_back_to_text_for_legacy_keys():
    assert cats_core.fact_kind("cat_1_likes_aaaaaaaa", "что угодно") == "likes"
    assert cats_core.fact_kind("тучка_еда", "Тучка не ест курицу") == "dislikes"
    assert cats_core.fact_kind("", "Тучка любит окно") == "likes"
    assert cats_core.fact_kind("", "аллергия на курицу") == "important"
    assert cats_core.fact_kind("", "спит на диване") == "other"


# ── путь сообщения ──────────────────────────────────────────────────────────

async def test_cat_fact_gets_cat_key_with_guessed_type():
    cid = crepo.create(MARK, "Тучка")
    r = await _store("Тучка не ест курицу", _haiku("Тучка не ест курицу", "тучка", "тучка_еда"))
    assert r["cat_id"] == cid and r["key"].startswith(f"cat_{cid}_dislikes_")
    (row,) = _rows(cid)
    assert row.key_name == r["key"] and row.related_to == "Тучка"


async def test_same_fact_twice_is_one_row_and_was_updated():
    cid = crepo.create(MARK, "Тучка")
    r1 = await _store("Тучка любит окно", _haiku("Тучка любит окно", "тучка"))
    r2 = await _store("тучке нравится окно", _haiku("  Тучка  любит ОКНО ", "тучке"))
    assert r1["was_updated"] is False and r2["was_updated"] is True
    assert r1["memory_id"] == r2["memory_id"] and len(_rows(cid)) == 1


async def test_different_facts_are_separate_rows_with_types():
    cid = crepo.create(MARK, "Тучка")
    await _store("a", _haiku("Тучка любит окно", "тучка"))
    await _store("b", _haiku("Тучка не ест курицу", "тучка"))
    await _store("c", _haiku("у Тучки аллергия на курицу", "тучка"))
    await _store("d", _haiku("Тучка спит на диване", "тучка"))
    kinds = sorted(cats_core.cat_key_type(r.key_name) for r in _rows(cid))
    assert kinds == ["dislikes", "important", "likes", "other"]


async def test_same_text_for_two_cats_does_not_collide():
    a = crepo.create(MARK, "Тучка")
    b = crepo.create(MARK, "Пончик")
    await _store("a", _haiku("Тучка любит окно", "тучка"))
    await _store("b", _haiku("Пончик любит окно", "пончик"))
    assert len(_rows(a)) == 1 and len(_rows(b)) == 1


async def test_unbound_cat_facts_keep_old_behavior():
    crepo.create(MARK, "Тучка")
    r = await _store("кот боится пылесоса", _haiku("боится пылесоса", "кот", "кот_страх"))
    assert r["cat_id"] is None and r["key"] == "кот_страх" and r["was_updated"] is False


# ── ответ бота ──────────────────────────────────────────────────────────────

async def _say(message_text, reply):
    sent = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    msg = SimpleNamespace(answer=AsyncMock(return_value=sent))
    with patch.object(mem, "ask_claude", AsyncMock(return_value=reply)), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await mem.save_memory(msg, message_text, MARK, "☀️ Nexus")
    return msg.answer.await_args_list[0].args[0]


async def test_repeat_reply_is_not_limit_wording():
    crepo.create(MARK, "Тучка")
    first = await _say("Тучка любит окно", _haiku("Тучка любит окно", "тучка"))
    again = await _say("Тучка любит окно", _haiku("Тучка любит окно", "тучка"))
    assert first.startswith("🧠 Запомнил [🐾 Коты]")
    assert again == "🧠 Уже знаю про кота: Тучка любит окно" and "лимит" not in again
