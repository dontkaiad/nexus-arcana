"""tests/test_cat_health_chat.py — здоровье котов, шаг 3: ввод из чата («Тучка весит 4,2 кг», «Пончик ветеринар 12 декабря», «Кексу капли с 15-го на 10 дней»).

Реальный путь сообщения: подменяется только граница LLM (core.memory.ask_claude), проверка, разбор ответа, резолв кота и запись в БД идут по-настоящему.
Главное: ровно ОДИН вызов LLM на сообщение (как и раньше), блок про здоровье только когда в тексте есть имя кота и слово про здоровье, любое сомнение —
обычный факт, ничего не теряется. Имена вымышленные."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, text

from core import cat_health_chat as chat
from core import memory as mem
from core.db import get_engine
from core.repos import pg_cat_health_repo as repo
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories

MARK, OTHER = "test-cat-health-chat", "test-cat-health-chat-other"
OWNER_TG = 700000123
TZ = 3


def today() -> date:
    return datetime.now(timezone(timedelta(hours=TZ))).date()


D = lambda n: today() + timedelta(days=n)  # noqa: E731


@pytest.fixture(autouse=True)
def _env():
    resched, cancel = AsyncMock(), MagicMock()
    with patch("core.cat_health.get_user_id", AsyncMock(return_value=MARK)), \
         patch("core.cat_health._today_tz", AsyncMock(side_effect=lambda tg: (today(), TZ))), \
         patch("core.memory._health_today", AsyncMock(side_effect=lambda tg: today())), \
         patch("miniapp.backend.routes.writes._reschedule_task_reminder", resched), \
         patch("miniapp.backend.routes.writes._cancel_task_jobs", cancel), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"):
        _env.resched = resched
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        c.execute(text("DELETE FROM tasks WHERE user_id IN (:a, :b)"), {"a": MARK, "b": OTHER})
        c.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def cat():
    return crepo.create(MARK, "Тучка")


def reply(fact, связь, key, health=None):
    d = {"fact": fact, "category": "🐾 Коты", "связь": связь, "ключ": key}
    if health is not None:
        d["здоровье"] = health
    return json.dumps(d, ensure_ascii=False)


async def say(message, llm_reply, owner_tg=OWNER_TG, user_id=MARK):
    llm = AsyncMock(return_value=llm_reply)
    with patch.object(mem, "ask_claude", llm):
        r = await mem.parse_and_store(message, user_id, "☀️ Nexus", owner_tg=owner_tg)
    return r, llm


def n(table, **where):
    with get_engine().connect() as c:
        cond = " AND ".join(f"{k} = :{k}" for k in where) or "TRUE"
        return c.execute(text(f"SELECT count(*) FROM {table} WHERE {cond}"), where).scalar()


def memories_of():
    with get_engine().connect() as c:
        return c.execute(text("SELECT count(*) FROM memories WHERE user_id = :u"), {"u": MARK}).scalar()


# ── проверка без LLM ──────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", ["Тучка весит 4,2 кг", "Тучке капли с 15-го на 10 дней", "тучку взвесила, 4300 г", "Пончик ветеринар 12 декабря",
                                 "у Пончика прививка в субботу", "Кексу дала таблетку", "Кекса от блох обработала", "Пончика отвезла на осмотр", "Тучка ёлка вес"])
def test_wants_health_positive(msg):
    assert chat.wants_health(msg, ["Тучка", "Пончик", "Кекс"]) is True


@pytest.mark.parametrize("msg", ["Тучка не ест курицу", "купить капли", "ветеринар в среду", "весь день дома", "Маша весит 60 кг", "курс валюты вырос, Пончик спит",
                                 "Тучка любит ёлку", "", "Рыжик весит 4 кг", "Тучка весь день дома", "Тут вес 4 кг"])
def test_wants_health_negative(msg):
    assert chat.wants_health(msg, ["Тучка", "Пончик", "Кекс"]) is False


def test_wants_health_without_cats_or_with_blank_names():
    assert chat.wants_health("Тучка весит 4 кг", []) is False and chat.wants_health("Тучка весит 4 кг", ["", "  "]) is False
    assert chat.wants_health("Бим весит 4 кг", ["Бим"]) is True                                 # a three-letter name matches whole


def test_prompt_block_is_short_names_today_and_has_no_real_data():
    b = chat.prompt_block(date(2026, 12, 1))
    assert "2026-12-01" in b and len(b) < 2500 and "здоровье" in b
    assert all(x in b for x in ("вес", "визит", "лекарство")) and "Тучка" not in b


# ── строгая проверка ответа модели ────────────────────────────────────────────────────────────────────────────────────

T = date(2026, 12, 1)


@pytest.mark.parametrize("raw,text,expect", [
    ({"тип": "вес", "кот": "Тучка", "граммы": 4200}, "", 4200),
    ({"тип": "вес", "кот": "Тучка", "граммы": "4200"}, "", 4200),
    ({"тип": "вес", "кот": "Тучка", "граммы": 4200.0}, "", 4200),
    ({"тип": "вес", "кот": "Тучка", "граммы": 4000}, "Тучка весит 4,2 кг", 4200),             # the number in the message beats the model's
    ({"тип": "вес", "кот": "Тучка", "граммы": 4000}, "Тучка весит 4.25кг", 4250),
    ({"тип": "вес", "кот": "Тучка", "граммы": 1}, "Тучка 3900 г", 3900),
    ({"тип": "вес", "кот": "Тучка", "граммы": 4000}, "Тучка 4,2 кг, ест 60 г", 4000),         # two numbers: ambiguous, the model's stays
    ({"тип": "вес", "кот": "Тучка", "граммы": 99}, "", None), ({"тип": "вес", "кот": "Тучка", "граммы": 30001}, "", None),
    ({"тип": "вес", "кот": "Тучка", "граммы": True}, "", None), ({"тип": "вес", "кот": "Тучка", "граммы": "много"}, "", None),
    ({"тип": "вес", "кот": "Тучка"}, "", None), ({"тип": "вес", "граммы": 4000}, "", None), ({"тип": "вес", "кот": " ", "граммы": 4000}, "", None),
    ({"тип": "неизвестно", "кот": "Тучка", "граммы": 4000}, "", None), ({"кот": "Тучка", "граммы": 4000}, "", None),
    ("вес", "", None), (None, "", None), ([], "", None), ({"тип": "вес", "кот": "я" * 61, "граммы": 4000}, "", None),
])
def test_validate_weight(raw, text, expect):
    item = chat.validate(raw, T, text)
    assert (item.grams if item else None) == expect


@pytest.mark.parametrize("raw,ok", [
    ({"тип": "визит", "кот": "Пончик", "название": "осмотр", "дата": "2026-12-12"}, True),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12"}, True),                              # no title → «ветеринар»
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-11-30"}, True),                              # yesterday: happened
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12", "вид": "vaccine", "следующий_срок": "2027-12-12"}, True),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12", "следующий_срок": ""}, True),
    ({"тип": "визит", "кот": "Пончик", "дата": "12 декабря"}, False), ({"тип": "визит", "кот": "Пончик", "дата": "2026-02-30"}, False),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-1"}, False), ({"тип": "визит", "кот": "Пончик"}, False),
    ({"тип": "визит", "кот": "Пончик", "дата": "2024-01-01"}, False), ({"тип": "визит", "кот": "Пончик", "дата": "2031-01-01"}, False),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12", "вид": "operation"}, False),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12", "следующий_срок": "2026-12-01"}, False),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12", "следующий_срок": "через год"}, False),
    ({"тип": "визит", "кот": "Пончик", "дата": "2026-12-12", "название": "я" * 121}, False),
])
def test_validate_visit(raw, ok):
    item = chat.validate(raw, T, "")
    assert (item is not None) is ok
    if ok:
        assert item.type == "visit" and item.title and item.kind in chat.KINDS


@pytest.mark.parametrize("raw,ok", [
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "начало": "2026-12-15", "дней": 10}, True),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли"}, True),                              # start defaults to today, no end
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "доза": "по 2 капли", "приёмов": 2, "дней": "7"}, True),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "дней": ""}, True),
    ({"тип": "лекарство", "кот": "Кекс"}, False), ({"тип": "лекарство", "кот": "Кекс", "название": ""}, False),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "дней": 0}, False), ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "дней": 366}, False),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "приёмов": 0}, False), ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "приёмов": 5}, False),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "доза": "я" * 81}, False),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "начало": "2026-01-01"}, False), ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "начало": "2028-01-01"}, False),
    ({"тип": "лекарство", "кот": "Кекс", "название": "капли", "дней": True}, False),
    ({"тип": "рецепт", "кот": "Кекс", "название": "капли"}, False), ({"тип": "", "кот": "Кекс", "название": "капли"}, False),
])
def test_validate_med(raw, ok):
    item = chat.validate(raw, T, "")
    assert (item is not None) is ok
    if ok and "начало" not in raw:
        assert item.on == T


def test_weight_from_text():
    f = chat.weight_from_text
    assert f("4,2 кг") == 4200 and f("4.2 килограмма") == 4200 and f("4200 г") == 4200 and f("4200 грамм") == 4200 and f("3 кг 4 кг") is None
    assert f("весит как-то") is None and f("") is None and f("4,2 кг и ещё 4,2 кг") == 4200


# ── сообщение целиком ─────────────────────────────────────────────────────────────────────────────────────────────────

async def test_weight_is_stored_in_one_llm_call_and_not_as_a_fact(cat):
    r, llm = await say("Тучка весит 4,2 кг", reply("Тучка весит 4,2 кг", "тучка", "тучка_вес", {"тип": "вес", "кот": "Тучка", "граммы": 4200}))
    assert llm.await_count == 1 and r["kind"] == "cat_health" and r["health"]["type"] == "weight" and r["cat_id"] == cat
    assert r["health"]["summary_html"] == "⚖️ Тучка: 4,2 кг" and r["memory_id"] is None and memories_of() == 0
    w = repo.list_weights(MARK, cat)
    assert [(x["grams"], x["weighed_on"]) for x in w] == [(4200, today())]


async def test_the_health_block_goes_into_the_same_request_only_when_needed(cat):
    _, llm = await say("Тучка весит 4,2 кг", reply("x", "тучка", "k"))
    assert "ЗДОРОВЬЕ КОТА" in llm.await_args.kwargs["system"] and today().isoformat() in llm.await_args.kwargs["system"]
    _, llm = await say("Тучка не ест курицу", reply("Тучка не ест курицу", "тучка", "тучка_еда"))
    assert "ЗДОРОВЬЕ КОТА" not in llm.await_args.kwargs["system"] and llm.await_count == 1
    _, llm = await say("Маша весит 60 кг", reply("Маша весит 60 кг", "маша", "маша_вес"))
    assert "ЗДОРОВЬЕ КОТА" not in llm.await_args.kwargs["system"]
    _, llm = await say("Тучка весит 4,2 кг", reply("x", "тучка", "k"), owner_tg=None)           # no owner id: no block
    assert "ЗДОРОВЬЕ КОТА" not in llm.await_args.kwargs["system"]


async def test_weight_number_comes_from_the_message_not_the_model(cat):
    r, _ = await say("Тучка весит 4,2 кг", reply("x", "тучка", "k", {"тип": "вес", "кот": "Тучка", "граммы": 4000}))
    assert r["kind"] == "cat_health" and repo.list_weights(MARK, cat)[0]["grams"] == 4200


async def test_the_same_weight_message_twice_is_one_row(cat):
    for _ in range(2):
        await say("Тучка весит 4,2 кг", reply("x", "тучка", "k", {"тип": "вес", "кот": "Тучка", "граммы": 4200}))
    assert n("cat_weights", cat_id=cat) == 1


async def test_planned_visit_gets_a_row_and_a_reminder_task_and_repeat_is_a_noop(cat):
    pid = crepo.create(MARK, "Пончик")
    h = {"тип": "визит", "кот": "Пончик", "название": "ветеринар", "дата": D(10).isoformat(), "вид": "visit", "следующий_срок": ""}
    r, llm = await say("Пончик ветеринар 12 декабря", reply("Пончик у ветеринара", "пончик", "пончик_ветеринар", h))
    assert llm.await_count == 1 and r["kind"] == "cat_health" and r["health"]["type"] == "visit" and r["health"]["duplicate"] is False
    assert r["health"]["task_linked"] is True and "напомню накануне в 09:00" in r["health"]["summary_html"]
    (v,) = repo.list_visits(MARK, pid)
    assert v["status"] == "planned" and v["visit_on"] == D(10) and v["task_id"] is not None and memories_of() == 0
    with get_engine().connect() as c:
        t = c.execute(text("SELECT cat_id, title FROM tasks WHERE id = :i"), {"i": v["task_id"]}).one()
    assert t.cat_id == pid and "Пончик" in t.title
    r2, _ = await say("Пончик ветеринар 12 декабря", reply("x", "пончик", "k", h))
    assert r2["health"]["duplicate"] is True and "(уже записано)" in r2["health"]["summary_html"] and len(repo.list_visits(MARK, pid)) == 1 and n("tasks", user_id=MARK) == 1


async def test_a_visit_in_the_past_is_recorded_as_done_without_a_task(cat):
    h = {"тип": "визит", "кот": "Тучка", "название": "прививка", "дата": D(-5).isoformat(), "вид": "vaccine", "следующий_срок": D(360).isoformat()}
    r, _ = await say("Тучке сделали прививку", reply("x", "тучка", "k", h))
    (v,) = repo.list_visits(MARK, cat)
    assert v["status"] == "done" and v["kind"] == "vaccine" and v["next_due"] == D(360)
    assert r["health"]["task_linked"] is True and "💉" in r["health"]["summary_html"] and "напомню накануне" not in r["health"]["summary_html"]
    with get_engine().connect() as c:                                                           # the reminder is for the NEXT term, not for the past visit
        assert c.execute(text("SELECT deadline FROM tasks WHERE id = :i"), {"i": v["task_id"]}).scalar().astimezone(timezone(timedelta(hours=TZ))).date() == D(360)


async def test_med_course_gets_a_row_and_a_daily_task_and_repeat_is_a_noop(cat):
    h = {"тип": "лекарство", "кот": "Тучка", "название": "капли", "доза": "", "начало": D(1).isoformat(), "дней": 10, "приёмов": 1}
    r, llm = await say("Тучке капли с завтрашнего дня на 10 дней", reply("x", "тучка", "k", h))
    assert llm.await_count == 1 and r["health"]["type"] == "med" and r["health"]["task_linked"] is True and "напоминаю каждый день в 09:00" in r["health"]["summary_html"]
    (m,) = repo.list_meds(MARK, cat)
    assert m["starts_on"] == D(1) and m["ends_on"] == D(10) and m["status"] == "active" and m["task_id"] is not None
    r2, _ = await say("Тучке капли с завтрашнего дня на 10 дней", reply("x", "тучка", "k", h))
    assert r2["health"]["duplicate"] is True and len(repo.list_meds(MARK, cat)) == 1 and n("tasks", user_id=MARK) == 1


@pytest.mark.parametrize("name,setup", [("unknown cat", "none"), ("two cats with the same name", "twins"), ("only a foreign cat", "foreign")])
async def test_cat_that_does_not_resolve_to_exactly_one_falls_back_to_a_plain_fact(name, setup):
    if setup == "twins":
        crepo.create(MARK, "Рыжик"); crepo.create(MARK, "Рыжик")
    if setup == "foreign":
        crepo.create(OTHER, "Рыжик")
    crepo.create(MARK, "Тучка")                                                                  # the gate needs some cat of the owner
    r, _ = await say("Рыжик весит 4,2 кг, Тучка спит", reply("Рыжик весит 4,2 кг", "рыжик", "рыжик_вес", {"тип": "вес", "кот": "Рыжик", "граммы": 4200}))
    assert r["kind"] == "memory" and memories_of() == 1
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM cat_weights w JOIN cats ct ON ct.id = w.cat_id WHERE ct.user_id IN (:a, :b)"), {"a": MARK, "b": OTHER}).scalar() == 0


@pytest.mark.parametrize("health", [{"тип": "вес", "кот": "Тучка", "граммы": 99999}, {"тип": "визит", "кот": "Тучка", "дата": "завтра"},
                                    {"тип": "лекарство", "кот": "Тучка", "название": "капли", "дней": 999}, {"тип": "вес", "кот": "Тучка"}, "вес 4 кг", ["вес"], 5])
async def test_invalid_model_answer_is_saved_as_a_plain_fact(cat, health):
    r, _ = await say("Тучка весит что-то", reply("Тучка весит что-то", "тучка", "тучка_вес", health))
    assert r["kind"] == "memory" and r["cat_id"] == cat and memories_of() == 1
    assert n("cat_weights", cat_id=cat) == 0 and n("cat_vet_visits", cat_id=cat) == 0 and n("cat_medications", cat_id=cat) == 0


async def test_health_key_is_ignored_when_the_text_gave_no_reason_for_it(cat):
    llm_reply = reply("Тучка любит спать", "тучка", "тучка_сон", {"тип": "вес", "кот": "Тучка", "граммы": 4200})
    r, _ = await say("Тучка любит спать", llm_reply)                                             # no health word: the model's extra key is not trusted
    assert r["kind"] == "memory" and n("cat_weights", cat_id=cat) == 0 and memories_of() == 1


async def test_a_storage_failure_falls_back_to_a_plain_fact(cat):
    with patch("core.cat_health.add_weight", AsyncMock(side_effect=RuntimeError("db down"))):
        r, _ = await say("Тучка весит 4,2 кг", reply("Тучка весит 4,2 кг", "тучка", "тучка_вес", {"тип": "вес", "кот": "Тучка", "граммы": 4200}))
    assert r["kind"] == "memory" and memories_of() == 1 and n("cat_weights", cat_id=cat) == 0


async def test_a_failed_reminder_keeps_the_record_and_says_so(cat):
    h = {"тип": "визит", "кот": "Тучка", "название": "осмотр", "дата": D(10).isoformat(), "вид": "visit"}
    with patch("core.cat_health._tasks.create", AsyncMock(side_effect=RuntimeError("db down"))):
        r, _ = await say("Тучка осмотр", reply("x", "тучка", "k", h))
    assert r["kind"] == "cat_health" and r["health"]["task_linked"] is False and "напомню" not in r["health"]["summary_html"] and len(repo.list_visits(MARK, cat)) == 1


async def test_names_and_titles_in_the_answer_are_html_escaped():
    cid = crepo.create(MARK, "<b>Тучка</b>")
    out = await chat.store(OWNER_TG, MARK, {"id": cid, "name": "<b>Тучка</b>"}, chat.Item("visit", "x", title="<i>осмотр</i>", on=D(10)), today())
    assert "<b>" not in out["summary_html"] and "<i>" not in out["summary_html"] and "&lt;b&gt;Тучка&lt;/b&gt;" in out["summary_html"] and "&lt;i&gt;осмотр&lt;/i&gt;" in out["summary_html"]
    out = await chat.store(OWNER_TG, MARK, {"id": cid, "name": "<b>Тучка</b>"}, chat.Item("med", "x", title="<u>капли</u>", on=D(1)), today())
    assert "<u>" not in out["summary_html"] and "&lt;u&gt;" in out["summary_html"]


async def test_other_messages_are_untouched(cat):
    r, llm = await say("Тучка не ест курицу", reply("Тучка не ест курицу", "тучка", "тучка_еда"))
    assert r["kind"] == "memory" and r["cat_id"] == cat and llm.await_count == 1 and memories_of() == 1


# ── бот и Mini App ────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_bot_replies_with_the_summary(cat):
    msg = SimpleNamespace(from_user=SimpleNamespace(id=OWNER_TG), answer=AsyncMock())
    with patch.object(mem, "ask_claude", AsyncMock(return_value=reply("x", "тучка", "k", {"тип": "вес", "кот": "Тучка", "граммы": 4200}))):
        await mem.save_memory(msg, "Тучка весит 4,2 кг", MARK, "☀️ Nexus")
    msg.answer.assert_awaited_once_with("⚖️ Тучка: 4,2 кг")


async def test_miniapp_fab_returns_the_health_result(cat):
    from miniapp.backend.routes import writes
    notify = AsyncMock()
    with patch.object(mem, "ask_claude", AsyncMock(return_value=reply("x", "тучка", "k", {"тип": "вес", "кот": "Тучка", "граммы": 4200}))), \
         patch.object(writes, "get_user_id", AsyncMock(return_value=MARK)), patch.object(writes, "notify_user", notify):
        out = await writes._memory_create(writes.NoteBody(text="Тучка весит 4,2 кг"), OWNER_TG)
    assert out["kind"] == "cat_health" and out["ok"] is True and out["health"]["type"] == "weight" and "summary_html" not in out["health"]
    notify.assert_awaited_once()


def test_classifier_routes_cat_health_to_memory_not_to_tasks():
    src = open("core/classifier.py", encoding="utf-8").read()
    assert "ПРАВИЛО здоровье кота" in src and "memory_save, НЕ task и НЕ task_done" in src
