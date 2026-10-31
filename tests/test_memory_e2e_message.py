"""tests/test_memory_e2e_message.py — сквозной путь: ТЕКСТ СООБЩЕНИЯ → классификатор → process_item → ответ бота.

Классификатор замокан в прод-формате (Haiku возвращает JSON-массив; `query` — обрезанные
ключевые слова, как в логах прода: «что не любит миша» → "миша"). Это ровно те две функции,
которые зовёт nexus_bot.handle_text (classify → process_item). БД реальная (DATABASE_URL), Voyage
выключен, Haiku-реранк замокан. Регрессия: answer() должен получать ИСХОДНЫЙ текст, а не query.

Два пути классификатора: Haiku (все пять вопросов) и regex-префильтр `_MEMORY_SEARCH_RE`
(«напомни про …», «что ты помнишь о …») — Haiku там не вызывается.
Privacy: вымышленные имена и факты; всё помечено user_id=MARK.
"""
from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, insert

from core import classifier
from core import memory_search as ms
from core.db import get_engine
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests._people_helpers import insert_person

MARK = "test-e2e-msg"
CAT = "👥 Люди"

# Как отвечает прод-классификатор (по логам): query — только ключевые слова.
HAIKU = {
    "что не любит миша": [{"type": "memory_search", "query": "миша"}],
    "что любит миша": [{"type": "memory_search", "query": "миша"}],
    "что я не ем": [{"type": "memory_search", "query": "не ем"}],
    "что важно про олю": [{"type": "memory_search", "query": "оля"}],
    "когда у оли др": [{"type": "memory_search", "query": "оля день рождения"}],
}


# ── окружение ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _env():
    with patch.object(ms, "_embed", return_value=[]):
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def _person(name, aliases=(), **cols):
    pid = insert_person(MARK, name, aliases=list(aliases), status="confirmed", **cols)
    with get_engine().begin() as c:
        prepo.sync_norm(c, pid)
    return pid


def _fact(pid, typ, text, status="confirmed", n=[0]):
    n[0] += 1
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text, key_name="person_%d_%s_%08x" % (pid, typ, n[0]), category=CAT, person_id=pid,
            user_id=MARK, review_status=status).returning(memories.c.id)).scalar_one()


def _own(text):
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(
            fact_text=text, key_name="", category="🛒 Предпочтения", user_id=MARK,
            review_status="confirmed").returning(memories.c.id)).scalar_one()


@pytest.fixture
def world():
    misha1 = _person("Михаил Тестов", ["Миша"], notes="коллега")
    misha2 = _person("Миша Образцов", ["Миша"])
    olya = _person("Ольга", ["Оля"], birthday=__import__("datetime").date(1990, 3, 5), birthday_year_known=True)
    return {
        "m1_dis": _fact(misha1, "dislikes", "не любит опозданий"),
        "m2_dis": _fact(misha2, "dislikes", "не любит жару"),
        "m1_like": _fact(misha1, "likes", "любит джаз"),
        "m2_like": _fact(misha2, "likes", "любит рок"),
        "o_imp": _fact(olya, "important", "аллергия на орехи"),
        "o_like": _fact(olya, "likes", "любит чай"),
        # типы, неверно поставленные Haiku при импорте (проверка пункта «без мусора»)
        "m1_scold": _fact(misha1, "important", "мать ругала"),
        "m1_pills": _fact(misha1, "important", "пьёт таблетки"),
        "m1_alone": _fact(misha1, "likes", "не любит ходить один"),
        "own": _own("не ем сладкое"),
        "olya": olya,
    }


def _rerank_returns_all_candidates():
    """Haiku-реранк: возвращает все кандидаты, что до него дошли (ограничение субъекта проверяем по SQL)."""
    async def fake(prompt="", **kw):
        return json.dumps([int(x) for x in re.findall(r"id=(\d+)", prompt)])
    return patch.object(ms, "ask_claude", AsyncMock(side_effect=fake))


class Bot:
    """Мок message + шпион за answer(): что реально получил поиск и что ответил бот."""

    def __init__(self, text):
        self.msg = MagicMock()
        self.msg.text = text
        self.msg.from_user.id = 7
        self.msg.answer = AsyncMock()
        self.msg.reply = AsyncMock()
        self.answered_with = []
        self.results = []

    @property
    def text(self):
        return "\n".join(str(c.args[0]) for c in self.msg.answer.await_args_list if c.args)


def run_message(text, haiku=None, rerank=True):
    """classify → process_item, как в nexus_bot.handle_text. Возвращает (Bot, classify_ask_mock)."""
    bot = Bot(text)
    real_answer = ms.answer

    async def spy(query, **kw):
        bot.answered_with.append(query)
        res = await real_answer(query, **kw)
        bot.results.append(res)
        return res

    classify_ask = AsyncMock(return_value=json.dumps(haiku if haiku is not None else HAIKU.get(text, [])))

    async def go():
        items = await classifier.classify(text, tz_offset=3, user_id=MARK)
        for data in items:
            await classifier.process_item(data, text, bot.msg, {}, user_id=MARK)
        return items

    with patch.object(classifier, "ask_claude", classify_ask), \
         patch("core.memory._memory_search.answer", spy), \
         patch("nexus.handlers.utils.react", AsyncMock()), \
         patch("core.memory._search_finance", AsyncMock(return_value=[])), \
         (_rerank_returns_all_candidates() if rerank else patch.object(ms, "ask_claude", AsyncMock(return_value="[]"))):
        bot.items = asyncio.run(go())
    return bot, classify_ask


# ── Haiku-путь: пять вопросов ────────────────────────────────────────────────

@pytest.mark.parametrize("text", list(HAIKU))
def test_answer_receives_original_text_not_classifier_query(world, text):
    bot, classify_ask = run_message(text)
    classify_ask.assert_awaited()                         # это путь Haiku, не regex
    assert bot.answered_with == [text]                    # ровно исходный текст сообщения


def test_dislikes_of_homonyms_two_labeled_groups(world):
    bot, _ = run_message("что не любит миша")
    t = bot.text
    assert "Михаил Тестов (коллега)" in t and "Миша Образцов" in t
    assert "не любит опозданий" in t and "не любит жару" in t
    assert "любит джаз" not in t and "любит рок" not in t     # likes не подмешиваются
    assert "👥 Люди (" not in t                                # не старый групповой заголовок
    assert "мать ругала" not in t and "пьёт таблетки" not in t    # important по ошибке Haiku ≠ «не любит»
    assert "не любит ходить один" in t                         # likes по ошибке Haiku, но текст — dislikes
    assert bot.results[0].route == "direct"


def test_likes_of_homonyms(world):
    bot, _ = run_message("что любит миша")
    t = bot.text
    assert "любит джаз" in t and "любит рок" in t
    assert "не любит" not in t.replace("любит джаз", "").replace("любит рок", "")
    assert "не любит ходить один" not in t and "мать ругала" not in t and "пьёт таблетки" not in t
    assert bot.results[0].route == "direct" and bot.results[0].types == ("likes",)


def test_important_about_olya_direct(world):
    bot, _ = run_message("что важно про олю")
    t = bot.text
    assert "аллергия на орехи" in t and "Ольга" in t
    assert "любит чай" not in t and "👥 Люди (" not in t
    assert bot.results[0].route == "direct" and bot.results[0].types == ("important",)


def test_important_about_olya_empty_is_dont_know(world):
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.id == world["o_imp"]))
    bot, _ = run_message("что важно про олю")
    assert "Не знаю" in bot.text and "Ольга" in bot.text
    assert "любит чай" not in bot.text                        # похожие факты не подсовываются


def test_i_dont_eat_only_my_facts(world):
    other = _person("Вера", ["Верочка"])
    _fact(other, "dislikes", "не ест сладкое")                # чужой и по словам похожий
    bot, _ = run_message("что я не ем")
    t = bot.text
    assert bot.results[0].route == "hybrid_self"
    assert "не ем сладкое" in t and "не ест сладкое" not in t
    assert "Ничего не нашёл" not in t


def test_birthday_from_card(world):
    bot, _ = run_message("когда у оли др")
    assert "05.03" in bot.text and bot.results[0].route == "birthday"


# ── regex-путь: Haiku не вызывается ──────────────────────────────────────────

@pytest.mark.parametrize("text,route,needle", [
    ("напомни про олю что важно", "direct", "аллергия на орехи"),
    ("расскажи про мишу что любит", "direct", "любит джаз"),
    ("что ты помнишь о том что не любит миша", "direct", "не любит жару"),
])
def test_regex_prefilter_path_also_passes_original_text(world, text, route, needle):
    bot, classify_ask = run_message(text, haiku=[])
    classify_ask.assert_not_awaited()                         # сработал _MEMORY_SEARCH_RE
    assert bot.items and bot.items[0]["type"] == "memory_search"
    assert bot.items[0]["query"] != text                      # query обрезан фильтром…
    assert bot.answered_with == [text]                        # …но поиск получил оригинал
    assert bot.results[0].route == route and needle in bot.text


def test_regex_path_direct_question_about_stored_fact(world):
    bot, classify_ask = run_message("какой у меня кз номер", haiku=[])
    classify_ask.assert_not_awaited()
    assert bot.answered_with == ["какой у меня кз номер"]
    assert bot.results[0].subject == "self"                   # «у меня» → только мои факты


# ── Arcana-обработчик ────────────────────────────────────────────────────────

def test_arcana_handler_uses_text_over_query(world):
    from arcana.handlers import memory as arc

    msg = MagicMock(); msg.from_user.id = 7; msg.answer = AsyncMock()
    seen = []
    real = ms.answer

    async def spy(q, **kw):
        seen.append(q)
        return await real(q, **kw)

    with patch("core.memory._memory_search.answer", spy), \
         patch("core.memory._search_finance", AsyncMock(return_value=[])), \
         _rerank_returns_all_candidates():
        asyncio.run(arc.handle_memory_search(msg, {"text": "что важно про олю", "query": "оля"}, MARK))
    assert seen == ["что важно про олю"]
    assert "аллергия на орехи" in msg.answer.await_args.args[0]
