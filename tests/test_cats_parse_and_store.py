"""tests/test_cats_parse_and_store.py — Cats subject, step 4: «🐾 Коты» в parse_and_store / save_memory.

Реальный путь сообщения: подменяется только граница LLM (core.memory.ask_claude → JSON-ответ Haiku), сам
_parse_fact, подстановка имён котов в промпт, резолв и запись в БД идут по-настоящему. Имена вымышленные.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, text

from core import memory as mem
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories

MARK = "test-cats-step4"
OTHER = "test-cats-step4-other"
CATEGORY = "🐾 Коты"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        conn.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


def _haiku(fact, связь, key, category=CATEGORY):
    return json.dumps({"fact": fact, "category": category, "связь": связь, "ключ": key}, ensure_ascii=False)


async def _store(message, reply_json, user_id=MARK):
    """Сообщение → parse_and_store; LLM подменён, возвращает (результат, mock ask_claude)."""
    llm = AsyncMock(return_value=reply_json)
    with patch.object(mem, "ask_claude", llm), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"):
        return await mem.parse_and_store(message, user_id, "☀️ Nexus"), llm


def _row(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT cat_id, person_id, category, related_to, fact_text, review_status "
                              "FROM memories WHERE id = :i"), {"i": int(mid)}).one()


# ── привязка ────────────────────────────────────────────────────────────────

async def test_known_cat_gets_cat_id():
    cid = crepo.create(MARK, "Тучка")
    r, _ = await _store("Тучка не ест курицу", _haiku("Тучка не ест курицу", "тучка", "тучка_еда"))
    assert r["kind"] == "memory" and r["cat_id"] == cid and r["cat_unknown"] is None
    row = _row(r["memory_id"])
    assert row.cat_id == cid and row.person_id is None
    assert row.related_to == "Тучка"                       # канонические имя карточки, не склонённое упоминание
    assert row.review_status == "confirmed"


async def test_declined_mention_resolves_to_same_cat():
    cid = crepo.create(MARK, "Тучка")
    r, _ = await _store("тучке нельзя курицу", _haiku("Тучке нельзя курицу", "тучке", "тучка_еда"))
    assert r["cat_id"] == cid


async def test_alias_resolves():
    cid = crepo.create(MARK, "Пончик", ["Пончо"])
    r, _ = await _store("Пончо боится пылесоса", _haiku("Пончо боится пылесоса", "пончо", "пончик_страх"))
    assert r["cat_id"] == cid


# ── без догадок ──────────────────────────────────────────────────────────────

async def test_generic_word_cat_saved_plain_without_hint():
    crepo.create(MARK, "Тучка")
    r, _ = await _store("кот боится пылесоса", _haiku("боится пылесоса", "кот", "кот_страх"))
    assert r["kind"] == "memory" and r["cat_id"] is None and r["cat_unknown"] is None
    row = _row(r["memory_id"])
    assert row.cat_id is None and row.category == CATEGORY and row.related_to == "кот"


async def test_unknown_cat_saved_plain_with_unknown_name():
    crepo.create(MARK, "Тучка")
    r, _ = await _store("Рыжик любит коробки", _haiku("Рыжик любит коробки", "рыжик", "рыжик_коробки"))
    assert r["kind"] == "memory" and r["cat_id"] is None and r["cat_unknown"] == "рыжик"
    assert _row(r["memory_id"]).cat_id is None


async def test_two_cats_same_name_no_binding_no_hint():
    crepo.create(MARK, "Тучка")
    crepo.create(MARK, "Тучка")
    r, _ = await _store("Тучка спит", _haiku("Тучка спит", "тучка", "тучка_сон"))
    assert r["cat_id"] is None and r["cat_unknown"] is None


async def test_other_users_cat_is_not_visible():
    crepo.create(OTHER, "Тучка")
    r, _ = await _store("Тучка спит", _haiku("Тучка спит", "тучка", "тучка_сон"))
    assert r["cat_id"] is None and r["cat_unknown"] == "тучка"


async def test_non_cat_category_never_touches_cats():
    crepo.create(MARK, "Тучка")
    r, _ = await _store("Тучка — моя подруга", _haiku("Тучка — подруга", "тучка", "тучка", category="👥 Люди"))
    assert r["cat_id"] is None and r["cat_unknown"] is None


# ── промпт ───────────────────────────────────────────────────────────────────

async def test_prompt_contains_cat_names_from_table_only():
    crepo.create(MARK, "Тучка", ["Туча"])
    crepo.create(MARK, "Пончик")
    crepo.create(OTHER, "Чужекот")
    _, llm = await _store("Тучка спит", _haiku("Тучка спит", "тучка", "тучка_сон"))
    system = llm.await_args.kwargs["system"]
    assert "Тучка, Туча, Пончик" in system and "Чужекот" not in system
    assert system.startswith(mem._PARSE_SYSTEM)            # статичный префикс не тронут (кеш промпта)


async def test_prompt_unchanged_without_cats():
    _, llm = await _store("я не ем сахар", _haiku("не ем сахар", "", "диета_сахар", category="🛒 Предпочтения"))
    assert llm.await_args.kwargs["system"] == mem._PARSE_SYSTEM


async def test_prompt_survives_cats_lookup_failure():
    with patch("core.repos.pg_cats_repo.cat_names", side_effect=RuntimeError("no table")):
        r, llm = await _store("я не ем сахар", _haiku("не ем сахар", "", "диета_сахар", category="🛒 Предпочтения"))
    assert r["kind"] == "memory" and llm.await_args.kwargs["system"] == mem._PARSE_SYSTEM


# ── ответ бота (save_memory) ─────────────────────────────────────────────────

def _msg():
    sent = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    return SimpleNamespace(answer=AsyncMock(return_value=sent))


async def _say(message_text, reply_json):
    msg = _msg()
    with patch.object(mem, "ask_claude", AsyncMock(return_value=reply_json)), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await mem.save_memory(msg, message_text, MARK, "☀️ Nexus")
    return msg.answer.await_args_list[0].args[0]


async def test_reply_has_hint_for_unknown_cat_without_buttons():
    reply = await _say("Рыжик любит коробки", _haiku("Рыжик любит коробки", "рыжик", "рыжик_коробки"))
    assert reply.startswith("🧠 Запомнил [🐾 Коты]: Рыжик любит коробки")
    assert "Кота рыжик нет в карточках, заведи в Mini App → Коты" in reply


async def test_reply_hint_is_html_escaped():
    reply = await _say("<b>Х</b> любит коробки", _haiku("любит коробки", "<b>х</b>", "х_коробки"))
    hint = reply.split("\n")[-1]                     # строка подсказки; эхо факта выше экранируется не здесь
    assert "<b>" not in hint and "Кота &lt;b&gt;х&lt;/b&gt; нет в карточках" in hint


async def test_reply_without_hint_for_known_and_generic():
    crepo.create(MARK, "Тучка")
    known = await _say("Тучка не ест курицу", _haiku("Тучка не ест курицу", "тучка", "тучка_еда"))
    generic = await _say("кот боится пылесоса", _haiku("боится пылесоса", "кот", "кот_страх"))
    assert "нет в карточках" not in known and "нет в карточках" not in generic
