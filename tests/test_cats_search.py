"""tests/test_cats_search.py — Cats phase 2, step 4: поиск и RAG по cat_id («что не ест X»).

Три слоя:
  1. core.query_parse — таблица разбора вопроса; резолв котов тем же нормализатором, что в проде;
  2. core.memory_search.answer — реальная БД (FTS; Voyage выключен), прямой запрос по cat_id + тип либо гибрид с
     фильтром cat_ids, совпадение имени кота и человека, отклонённый кот, чужой владелец;
  3. сквозной путь: ТЕКСТ СООБЩЕНИЯ → классификатор → process_item → ответ бота; Mini App GET /api/memory?q=.
LLM подменён везде; считаем вызовы: новых вызовов LLM у определения кота нет. Имена вымышленные.
"""
from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert

from core import classifier
from core import memory as mem
from core import memory_answer
from core import memory_search as ms
from core import query_parse as qp
from core.db import get_engine
from core.people import aliases_norm_for, mention_candidates
from core.repos import pg_cats_repo as crepo
from core.repos import pg_people_repo as prepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests._people_helpers import insert_person

MARK = "test-cats-p2-search"
OTHER = "test-cats-p2-search-other"
CATEGORY = "🐾 Коты"
TG = 700000055


# ═════════════ 1. разбор вопроса (чистая функция) ═════════════

CATS = [
    {"id": 1, "name": "Тучка", "aliases": ["Туча"]},
    {"id": 2, "name": "Пончик", "aliases": []},
    {"id": 3, "name": "Мира", "aliases": []},
    {"id": 4, "name": "Мира", "aliases": []},            # второй кот с тем же именем
]
PEOPLE = [{"id": 10, "name": "Тучка", "aliases": []}]    # человек с тем же именем, что у кота
for _x in CATS + PEOPLE:
    _x["norm"] = set(aliases_norm_for(_x["name"], _x["aliases"]))
    _x["birthday"], _x["birthday_year_known"] = None, True


def _match(pool, cands):
    forms = set(mention_candidates(cands))
    return [x for x in pool if x["norm"] & forms]


def parse(q, with_people=False, with_cats=True, facts_words=()):
    resolve_people = (lambda c: _match(PEOPLE, c)) if with_people else (lambda c: [])
    resolve_cats = (lambda c: _match(CATS, c)) if with_cats else None
    return qp.parse(q, resolve_people, lambda w: w in facts_words, resolve_cats)


def cat_ids(p):
    return sorted(c["id"] for c in p.cats)


@pytest.mark.parametrize("q,types", [
    ("что не ест тучка", ("dislikes",)),
    ("что не ест тучку", ("dislikes",)),                    # падежи
    ("что любит тучке", ("likes",)),
    ("что важно про тучку", ("important",)),
    ("что не любит туча", ("dislikes",)),                   # кличка
    ("какая аллергия у пончика", ("important",)),
])
def test_cat_subject_with_type(q, types):
    p = parse(q)
    assert p.subject == "cat" and p.types == types and p.people == []
    assert cat_ids(p) in ([1], [2])


def test_medicine_question_has_cat_but_no_type_so_hybrid():
    p = parse("какие лекарства у тучки")
    assert p.subject == "cat" and cat_ids(p) == [1] and p.types == ()
    assert not p.structured                                   # типа нет → гибрид внутри фактов кота


def test_structured_for_cat_needs_type_and_ignores_birthday():
    assert parse("что не ест тучка").structured
    assert not parse("какие лекарства у тучки").structured
    p = parse("когда у тучки день рождения")
    assert p.subject == "cat" and p.intent == "birthday" and not p.structured


def test_cat_and_person_with_same_name_is_ambiguous_not_guessed():
    p = parse("что не ест тучка", with_people=True)
    assert p.subject == "ambiguous" and cat_ids(p) == [1] and [x["id"] for x in p.people] == [10]


def test_two_cats_with_same_name_is_ambiguous():
    p = parse("что любит мира")
    assert p.subject == "ambiguous" and cat_ids(p) == [3, 4]


def test_mixed_with_self_stays_unclear():
    assert parse("что я люблю из того, что любит тучка").subject == "unclear"


def test_unknown_name_behaviour_is_unchanged():
    assert parse("что любит рыжик").subject == "unknown_person"                       # не кот и не человек
    assert parse("что любит рыжик", facts_words=("рыжик",)).subject == "unclear"      # слово встречается в фактах


def test_generic_word_is_not_a_cat():
    assert parse("что любит кот").cats == [] and parse("что любят коты").cats == []


def test_without_cat_resolver_parse_is_as_before():
    p = parse("что не ест тучка", with_cats=False)
    assert p.cats == [] and p.subject != "cat"
    assert qp.parse("что любит оле", lambda c: [], None).subject == "unknown_person"   # старая сигнатура работает


# ═════════════ 2. answer() на реальной БД ═════════════

@pytest.fixture(autouse=True)
def _env():
    with patch.object(ms, "_embed", return_value=[]):         # Voyage выключен → только FTS
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        c.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))
        c.execute(delete(people).where(people.c.user_id.in_([MARK, OTHER])))


def cat(name, aliases=(), user=MARK):
    return crepo.create(user, name, list(aliases))


def fact(cat_id, text_, key=None, user=MARK, **kw):
    vals = dict(fact_text=text_, key_name=key if key is not None else f"k_{abs(hash(text_)) % 10**6}",
                category=CATEGORY, related_to="", user_id=user, review_status="confirmed", cat_id=cat_id)
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


def person(name, aliases=()):
    pid = insert_person(MARK, name, aliases=list(aliases), status="confirmed")
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)
    return pid


def pfact(pid, text_):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text_, key_name="person_%d_other_%08x" % (pid, abs(hash(text_)) % 16**8), category="👥 Люди",
            person_id=pid, user_id=MARK, review_status="confirmed").returning(memories.c.id)).scalar_one()


@pytest.fixture
def world():
    t, p = cat("Тучка", ["Туча"]), cat("Пончик")
    return {
        "t": t, "p": p,
        "t_dis": fact(t, "Тучка не ест курицу", key=f"cat_{t}_dislikes_aaaaaaaa"),
        "t_dis_legacy": fact(t, "Тучка не любит рыбу", key="тучка_еда"),          # старый ключ: тип по тексту
        "t_like": fact(t, "Тучка любит окно", key=f"cat_{t}_likes_bbbbbbbb"),
        "t_imp": fact(t, "у Тучки аллергия на курицу", key=f"cat_{t}_important_cccccccc"),
        "t_med": fact(t, "Тучка принимает лекарства по утрам", key=f"cat_{t}_other_dddddddd"),
        "p_dis": fact(p, "Пончик не ест сыр", key=f"cat_{p}_dislikes_eeeeeeee"),
        "p_med": fact(p, "Пончик принимает лекарства вечером", key=f"cat_{p}_other_ffffffff"),
        "general": fact(None, "кошкам нужны лекарства от глистов", key="общее"),
    }


def ask(q, **kw):
    kw.setdefault("do_rerank", False)
    return asyncio.run(ms.answer(q, user_id=MARK, **kw))


def hit_ids(res):
    return sorted(int(h.memory.id) for h in res.hits)


def test_direct_dislikes_of_one_cat_only(world):
    res = ask("что не ест тучка")
    assert res.route == "direct" and res.kind == "facts" and res.subject == "cat" and res.types == ("dislikes",)
    (g,) = res.groups
    assert g.cat_id == world["t"] and g.label == "Тучка" and g.person_id is None
    assert hit_ids(res) == sorted([world["t_dis"], world["t_dis_legacy"]])     # не likes, не важное, не Пончик, не общее


def test_direct_important_declension_and_alias(world):
    for q in ("что важно про тучку", "что важно про тучи", "что важно про туча"):
        res = ask(q)
        assert res.route == "direct" and hit_ids(res) == [world["t_imp"]], q


def test_direct_empty_is_honest_dont_know(world):
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.id == world["p_dis"]))
    res = ask("что не ест пончик")
    assert res.route == "direct" and res.kind == "none" and res.hits == []
    text = mem._structured_text(res)
    assert "Не знаю" in text and "Пончик" in text and "тучк" not in text.lower()


def test_hybrid_filters_by_cat_ids_for_untyped_question(world):
    res = ask("какие лекарства у тучки")
    assert res.route == "hybrid_cat" and res.subject == "cat" and res.kind == "facts"
    own = {world[k] for k in ("t_dis", "t_dis_legacy", "t_like", "t_imp", "t_med")}
    assert set(hit_ids(res)) <= own                                            # только факты Тучки: ни Пончик, ни общий
    assert world["p_med"] not in hit_ids(res) and world["general"] not in hit_ids(res)
    assert int(res.hits[0].memory.id) == world["t_med"]                        # совпали оба слова — выше остальных


def test_hybrid_other_cat_gets_only_its_facts(world):
    res = ask("какие лекарства у пончика")
    assert set(hit_ids(res)) <= {world["p_dis"], world["p_med"]} and int(res.hits[0].memory.id) == world["p_med"]


def test_cat_and_person_same_name_both_groups_no_guessing(world):
    pid = person("Тучка")
    pf = pfact(pid, "не ест мясо")
    res = ask("что не ест тучка")
    assert res.route == "direct" and res.subject == "ambiguous"
    assert [(g.person_id, g.cat_id) for g in res.groups] == [(pid, None), (None, world["t"])]   # люди, затем коты
    assert pf in hit_ids(res) and world["t_dis"] in hit_ids(res) and world["p_dis"] not in hit_ids(res)
    text = mem._structured_text(res)
    assert "👤" in text and "🐈" in text


def test_cat_and_person_same_name_hybrid_searches_both_only(world):
    pid = person("Тучка")
    pf = pfact(pid, "принимает лекарства раз в день")
    other_person = pfact(person("Мария"), "принимает лекарства")
    res = ask("какие лекарства у тучки")
    assert res.route == "hybrid_person" and res.subject == "ambiguous"
    ids = hit_ids(res)
    assert pf in ids and world["t_med"] in ids
    assert other_person not in ids and world["p_med"] not in ids and world["general"] not in ids


def test_rejected_cat_is_not_resolved_and_its_facts_are_hidden(world):
    crepo.update_cat(MARK, world["p"], {"status": "rejected"})
    res = ask("что не ест пончик")
    assert res.subject != "cat" and world["p_dis"] not in hit_ids(res)
    gen = ask("лекарства")                                                      # общий поиск не достаёт факты убранного кота
    assert world["p_med"] not in hit_ids(gen) and world["t_med"] in hit_ids(gen)


def test_other_owners_cat_is_invisible(world):
    other = cat("Рыжик", user=OTHER)
    fact(other, "Рыжик не ест рыбу", user=OTHER)
    res = ask("что не ест рыжик")
    assert res.route == "unknown_person" and res.hits == []


def test_archived_and_noncurrent_cat_facts_are_not_returned(world):
    fact(world["t"], "Тучка не ест сметану", key=f"cat_{world['t']}_dislikes_00000001", is_archived=True)
    fact(world["t"], "Тучка не ест творог", key=f"cat_{world['t']}_dislikes_00000002", is_current=False)
    texts = [h.memory.fact for h in ask("что не ест тучка").hits]
    assert "Тучка не ест сметану" not in texts and "Тучка не ест творог" not in texts


def test_people_questions_unchanged_when_cats_exist(world):
    pid = person("Ольга", ["Оля"])
    pfact(pid, "не любит шум")
    res = ask("что не любит оля")
    assert res.route == "direct" and res.subject == "person" and [g.person_id for g in res.groups] == [pid]


# ═════════════ без новых вызовов LLM ═════════════

def test_no_llm_calls_for_cat_direct_and_unreranked_hybrid(world):
    llm = AsyncMock(return_value="[]")
    with patch.object(ms, "ask_claude", llm):
        ask("что не ест тучка")                                  # прямой запрос: SQL
        ask("какие лекарства у тучки")                           # гибрид без реранка: FTS
    llm.assert_not_awaited()


def test_hybrid_rerank_is_the_single_existing_haiku_call(world):
    async def fake(prompt="", **kw):
        return json.dumps([int(x) for x in re.findall(r"id=(\d+)", prompt)])
    llm = AsyncMock(side_effect=fake)
    with patch.object(ms, "ask_claude", llm):
        res = ask("какие лекарства у тучки", do_rerank=True)
    own = {world[k] for k in ("t_dis", "t_dis_legacy", "t_like", "t_imp", "t_med")}
    assert llm.await_count == 1                                                 # тот же один реранк, что у людей
    assert set(hit_ids(res)) <= own and world["p_med"] not in hit_ids(res)      # кандидаты реранка — только факты Тучки


def test_hybrid_cat_route_is_eligible_for_generated_answer(world, monkeypatch):
    monkeypatch.setenv("MEMORY_GENERATE_ANSWER", "1")
    fact(world["t"], "Тучка принимает лекарства вечером тоже", key=f"cat_{world['t']}_other_99999999")
    res = ask("какие лекарства у тучки")
    assert res.route == "hybrid_cat" and len(res.hits) >= 2 and memory_answer.eligible(res)
    groups = memory_answer.build_groups(res)
    facts = [f.fact for g in groups for f in g.facts]
    assert facts and all("Тучк" in f for f in facts) and "Пончик принимает лекарства вечером" not in facts


# ═════════════ 3. сквозной путь: сообщение → бот ═════════════

class Bot:
    def __init__(self, text):
        self.msg = MagicMock()
        self.msg.text = text
        self.msg.from_user.id = 7
        self.msg.answer = AsyncMock()
        self.msg.reply = AsyncMock()
        self.answered_with, self.results = [], []

    @property
    def text(self):
        return "\n".join(str(c.args[0]) for c in self.msg.answer.await_args_list if c.args)


def run_message(text, query):
    """classify → process_item, как nexus_bot.handle_text; классификатор отвечает как прод (query — ключевые слова)."""
    bot = Bot(text)
    real_answer = ms.answer

    async def spy(q, **kw):
        bot.answered_with.append(q)
        res = await real_answer(q, user_id=kw.pop("user_id", MARK), **kw)
        bot.results.append(res)
        return res

    async def rerank_all(prompt="", **kw):
        return json.dumps([int(x) for x in re.findall(r"id=(\d+)", prompt)])

    classify_ask = AsyncMock(return_value=json.dumps([{"type": "memory_search", "query": query}]))

    async def go():
        for data in await classifier.classify(text, tz_offset=3, user_id=MARK):
            await classifier.process_item(data, text, bot.msg, {}, user_id=MARK)

    with patch.object(classifier, "ask_claude", classify_ask), \
         patch("core.memory._memory_search.answer", spy), \
         patch("nexus.handlers.utils.react", AsyncMock()), \
         patch("core.memory._search_finance", AsyncMock(return_value=[])), \
         patch.object(ms, "ask_claude", AsyncMock(side_effect=rerank_all)):
        asyncio.run(go())
    return bot


def test_message_what_does_the_cat_not_eat(world):
    bot = run_message("что не ест тучка", "тучка")
    t = bot.text
    assert bot.answered_with == ["что не ест тучка"]                  # поиск получил исходный текст, не query
    assert bot.results[0].route == "direct"
    assert "🐈" in t and "Тучка" in t and "не ест курицу" in t and "не любит рыбу" in t
    assert "любит окно" not in t and "Пончик" not in t and "аллергия" not in t


def test_message_important_about_the_cat(world):
    t = run_message("что важно про тучку", "тучка").text
    assert "аллергия на курицу" in t and "не ест курицу" not in t and "🐈" in t


def test_message_untyped_question_is_hybrid_within_the_cat(world):
    bot = run_message("какие лекарства у пончика", "пончик лекарства")
    assert bot.results[0].route == "hybrid_cat"
    assert "Пончик принимает лекарства вечером" in bot.text
    assert "Тучка принимает" not in bot.text and "кошкам нужны" not in bot.text


def test_message_unknown_cat_is_not_invented(world):
    bot = run_message("что не ест рыжик", "рыжик")
    assert "Не нашёл в Людях «рыжик»" in bot.text and "Тучка" not in bot.text


# ═════════════ Mini App: GET /api/memory?q= ═════════════

def test_miniapp_search_finds_cat_facts_by_question(world):
    app.dependency_overrides[current_user_id] = lambda: TG
    try:
        with patch("miniapp.backend.routes.memory.get_user_id", AsyncMock(return_value=MARK)):
            d = TestClient(app).get("/api/memory", params={"q": "что не ест тучка"}).json()
    finally:
        app.dependency_overrides.clear()
    ids = {i["id"] for i in d["items"]}
    assert str(world["t_dis"]) in ids and str(world["t_dis_legacy"]) in ids
    assert str(world["t_like"]) not in ids and str(world["p_dis"]) not in ids
