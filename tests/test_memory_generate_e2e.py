"""tests/test_memory_generate_e2e.py — сквозной путь с ответом своими словами:
сообщение → classify (Haiku замокан в прод-формате) → process_item → search_memory → generate → ответ + кнопка.

Реальный Postgres; Voyage выключен; генерация (Haiku 5.5) и реранк замоканы. Вымышленные имена.
Проверяет: пять вопросов, grounding-fallback на список, кнопку «Все факты (N)» и работу чекбоксов после неё,
что ДР / «не знаю» / один факт идут без LLM, что состояние кнопки переживает «рестарт» (SQLite pending_kv).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from core import classifier, memory as mem, memory_answer as ma, pending_kv
from core import memory_search as ms
from core.db import get_engine
# фикстуры и хелперы данных — из сквозного теста поиска
from tests.test_memory_e2e_message import (  # noqa: F401
    HAIKU, Bot, _env, _fact, _own, _person, _rerank_returns_all_candidates, world,
)


@pytest.fixture(autouse=True)
def _gen(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_GENERATE_ANSWER", "1")
    monkeypatch.setattr(pending_kv, "_DB_PATH", str(tmp_path / "pending_kv.db"))
    with get_engine().connect() as c:
        last = c.execute(text("SELECT coalesce(max(id), 0) FROM memory_answer_log")).scalar()
    yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM memory_answer_log WHERE id > :i"), {"i": last})


def _gen_reply(by_group_text):
    """Мок генерации: строит валидный JSON из подготовленных фраз {номер группы: [(фраза, [ids])]}"""
    return json.dumps({"blocks": [{"group": g, "lines": [{"t": t, "ids": ids} for t, ids in lines]}
                                  for g, lines in by_group_text.items()]}, ensure_ascii=False)


def run(text_, gen_raw, haiku=None):
    bot = Bot(text_)
    real_answer = ms.answer
    results = []

    async def spy(query, **kw):
        res = await real_answer(query, **kw)
        results.append(res)
        return res

    gen_ask = AsyncMock(return_value=gen_raw)
    classify_ask = AsyncMock(return_value=json.dumps(haiku if haiku is not None else HAIKU.get(text_, [])))

    async def go():
        items = await classifier.classify(text_, tz_offset=3, user_id="test-e2e-msg")
        for data in items:
            await classifier.process_item(data, text_, bot.msg, {}, user_id="test-e2e-msg")

    with patch.object(classifier, "ask_claude", classify_ask), patch.object(ma, "ask_claude", gen_ask), \
         patch("core.memory._memory_search.answer", spy), patch("nexus.handlers.utils.react", AsyncMock()), \
         patch("core.memory._search_finance", AsyncMock(return_value=[])), _rerank_returns_all_candidates():
        asyncio.run(go())
    bot.results, bot.gen_ask = results, gen_ask
    return bot


def sent(bot):
    c = bot.msg.answer.await_args
    return c.args[0], c.kwargs.get("reply_markup")


def test_dislikes_of_homonyms_generated_paragraphs_and_button(world):
    ids = lambda *ks: [world[k] for k in ks]
    raw = _gen_reply({1: [("опоздания", ids("m1_dis")), ("ходить один", ids("m1_alone"))],
                      2: [("жару", ids("m2_dis"))]})
    # группы идут в порядке resolve (по id людей): первый — Михаил Тестов, второй — Миша Образцов
    bot = run("что не любит миша", raw)
    text_, kb = sent(bot)
    assert len(bot.results[0].groups) == 2
    gen_prompt = bot.gen_ask.await_args.kwargs["prompt"]
    assert "что не любит" in gen_prompt and "не любит опозданий" in gen_prompt
    assert "мать ругала" not in gen_prompt and "пьёт таблетки" not in gen_prompt   # неверно типизированные не входят
    paras = text_.split("\n\n")
    assert paras[0].startswith("<b>Михаил Тестов (коллега)</b> не любит: опоздания, ходить один.")
    assert paras[1].startswith("<b>Миша Образцов</b> не любит: жару.") and "добавлено вручную" in text_
    assert kb is not None and kb.inline_keyboard[0][0].text.startswith("Все факты (")
    assert kb.inline_keyboard[0][0].callback_data.startswith("memall:")


def _group_ids(world, bot):
    """{person_id: ids} в порядке групп результата — чтобы строить ответ под реальные номера групп."""
    return [[int(h.memory.id) for h in g.hits] for g in bot.results[0].groups]


def test_valid_answer_replaces_list_for_important_about_olya(world):
    probe = run("что важно про олю", "[]")                       # первый прогон — узнаём состав групп
    gids = _group_ids(world, probe)[0]
    assert len(gids) >= 1
    # у Оли в фикстуре один важный факт → ≥2 фактов нет → без LLM (как есть)
    assert probe.gen_ask.await_count == 0
    assert "аллергия на орехи" in probe.text


def test_two_facts_generated_and_kb_expands_with_checkboxes(world):
    # добавляем второй важный факт Оле → теперь ≥2 → генерация
    olya = world["olya"]
    extra = _fact(olya, "important", "нельзя кофеин")
    probe = run("что важно про олю", "[]")
    ids = _group_ids(world, probe)[0]
    assert sorted(ids) == sorted([world["o_imp"], extra])
    raw = _gen_reply({1: [("аллергия на орехи", [world["o_imp"]]), ("кофеин нельзя", [extra])]})
    bot = run("что важно про олю", raw)
    text_, kb = sent(bot)
    assert text_.startswith("<b>Ольга</b>, важное: аллергия на орехи, кофеин нельзя.")
    assert "добавлено вручную" in text_                 # у фикстур нет платформы → источник «вручную»

    # нажатие «Все факты (2)»
    call = MagicMock()
    call.from_user.id = 7
    call.data = kb.inline_keyboard[0][0].callback_data
    call.answer = AsyncMock()
    call.message.answer = AsyncMock()
    asyncio.run(mem.expand_all_facts(call))
    list_text, list_kb = call.message.answer.await_args.args[0], call.message.answer.await_args.kwargs["reply_markup"]
    assert "аллергия на орехи" in list_text and "нельзя кофеин" in list_text
    labels = [b.text for row in list_kb.inline_keyboard for b in row]
    assert any(l.startswith("☐") for l in labels)                           # чекбоксы на месте
    assert len(mem._mem_delete_pages[7]) == 2                               # «неактуально 1/2» работает как раньше


def test_button_state_survives_restart(world, tmp_path):
    olya = world["olya"]
    extra = _fact(olya, "important", "нельзя кофеин")
    raw = _gen_reply({1: [("аллергия на орехи", [world["o_imp"]]), ("кофеин нельзя", [extra])]})
    bot = run("что важно про олю", raw)
    _, kb = sent(bot)
    token = kb.inline_keyboard[0][0].callback_data.split(":")[1]
    # «рестарт»: in-memory словари пусты, состояние — только в SQLite
    mem._mem_delete_pages.clear(); mem._last_memory_results.clear()
    assert pending_kv.get(7, f"memall_{token}") is not None


def test_expired_button_is_polite():
    call = MagicMock(); call.from_user.id = 7; call.data = "memall:deadbeef"
    call.answer = AsyncMock(); call.message.answer = AsyncMock()
    asyncio.run(mem.expand_all_facts(call))
    assert "устарел" in call.message.answer.await_args.args[0]


def test_grounding_violation_falls_back_to_current_list(world):
    bad = _gen_reply({1: [("опоздания", [world["m1_dis"], 999999])]})
    bot = run("что не любит миша", bad)
    text_, kb = sent(bot)
    assert "Михаил Тестов (коллега)" in text_ and "не любит опозданий" in text_      # прежний структурный список
    assert "по фактам из" not in text_
    assert kb is not None and not any(
        b.callback_data.startswith("memall:") for row in kb.inline_keyboard for b in row)   # чекбоксы, не «Все факты»
    with get_engine().connect() as c:
        row = c.execute(text("SELECT outcome, reason, fact_ids FROM memory_answer_log ORDER BY id DESC LIMIT 1")).one()
    assert row.outcome == "fallback" and row.reason == "check_2" and row.fact_ids == [999999]


def test_llm_failure_falls_back_to_list(world):
    bot = run("что не любит миша", "")
    text_, _ = sent(bot)
    assert "не любит опозданий" in text_ and "по фактам из" not in text_


def test_birthday_and_dont_know_and_unknown_never_call_llm(world):
    for q in ("когда у оли др",):
        bot = run(q, "{}")
        assert bot.gen_ask.await_count == 0 and "05.03" in bot.text
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM memories WHERE id = :i"), {"i": world["o_imp"]})
    bot = run("что важно про олю", "{}")
    assert bot.gen_ask.await_count == 0 and "Не знаю" in bot.text


def test_i_dont_eat_generated_only_from_my_facts(world):
    own2 = _own("не ем рыбу")
    probe = run("что я не ем", "[]")
    ids = _group_ids(world, probe)[0]
    assert set(ids) <= {world["own"], own2}
    raw = _gen_reply({1: [("сладкое", [world["own"]]), ("рыбу", [own2])]})
    bot = run("что я не ем", raw)
    text_, kb = sent(bot)
    assert text_.startswith("Сладкое, рыбу.") and kb.inline_keyboard[0][0].text.startswith("Все факты")


def test_flag_off_keeps_current_behavior(world, monkeypatch):
    monkeypatch.setenv("MEMORY_GENERATE_ANSWER", "0")
    bot = run("что не любит миша", _gen_reply({1: [("x", [world["m1_dis"]])]}))
    assert bot.gen_ask.await_count == 0 and "не любит опозданий" in sent(bot)[0]
