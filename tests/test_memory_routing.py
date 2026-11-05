"""tests/test_memory_routing.py — «Запомни …» по субъекту: я → «Я», известный человек → карточка, тёзки → кнопки,
неизвестное имя → «Создать карточку?», «мы» и прочее → Разное. LLM замокан (граница ask_claude), имена вымышленные."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, text

from core import memory as mem
from core import memory_routing as mr
from core.db import get_engine
from core.repos.memories_table import memories
from core.repos.people_tables import people
from tests._people_helpers import insert_person

MARK = "test-memory-routing"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(people).where(people.c.user_id == MARK))


def _reply(fact, category, link="", key="x"):
    return json.dumps({"fact": fact, "category": category, "связь": link, "ключ": key}, ensure_ascii=False)


async def _store(text_, reply):
    with patch.object(mem, "ask_claude", AsyncMock(return_value=reply)), \
         patch.object(mem, "_resolve_alias", AsyncMock(return_value=None)), \
         patch("core.repos.pg_memory_repo._spawn_index"):
        return await mem.parse_and_store(text_, MARK, "☀️ Nexus")


def _row(mid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT person_id, category, review_status, source FROM memories WHERE id = :i"),
                         {"i": int(mid)}).one()


def _count():
    with get_engine().connect() as c:
        return c.execute(text("SELECT count(*) FROM memories WHERE user_id = :u"), {"u": MARK}).scalar()


def _person(name, aliases, **kw):
    from core.repos import pg_people_repo as prepo
    return prepo.create(MARK, name, aliases, status="confirmed", **kw)


def _me():
    return insert_person(MARK, "Кай", is_self=True, status="confirmed", aliases=["Кай"])


# ── чистый маршрут ────────────────────────────────────────────────────────

def test_first_person_detector():
    assert mr.first_person("у меня аллергия на пыль") and mr.first_person("не ем сахар") and mr.first_person("я устаю")
    assert not mr.first_person("кот боится пылесоса")


def test_self_category_keeps_own_and_maps_foreign():
    assert mr.self_category("🦋 СДВГ", "х") == "🦋 СДВГ"
    assert mr.self_category("🏥 Здоровье", "х") == "🏥 Здоровье"
    assert mr.self_category("💡 Инсайт", "странный факт") == "🗂 Прочее"
    assert mr.self_category("👥 Люди", "любит рисовать") == "🎮 Хобби"


def test_route_keeps_system_and_cats():
    assert mr.route_subject("лимит на кафе 5000", "💰 Лимит", "кафе", "лимит_кафе", MARK) == {"to": "keep"}
    assert mr.route_subject("у меня кот", "🐾 Коты", "кот", "кот", MARK) == {"to": "keep"}
    assert mr.route_subject("у нас дома тихо", "🏠 Быт", "", "тихо", MARK)["to"] == "misc"


# ── parse_and_store ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_about_me_goes_to_self_card_with_own_category():
    me = _me()
    r = await _store("у меня аллергия на пыль", _reply("аллергия на пыль", "🏥 Здоровье"))
    assert r["kind"] == "memory" and r["person_id"] == me
    row = _row(r["memory_id"])
    assert row.person_id == me and row.category == "🏥 Здоровье" and row.review_status == "confirmed" and row.source == "manual"


@pytest.mark.asyncio
async def test_adhd_category_is_kept_for_self():
    me = _me()
    r = await _store("в гиперфокусе нельзя отвлекать", _reply("в гиперфокусе нельзя отвлекать", "🦋 СДВГ", key="гиперфокус"))
    # первого лица в тексте нет → остаётся как раньше (Разное): маршрут не угадывает «про себя» без маркера
    assert _row(r["memory_id"]).person_id is None
    r2 = await _store("мне мешает белый шум", _reply("белый шум мешает", "🦋 СДВГ", key="шум"))
    assert _row(r2["memory_id"]).person_id == me and _row(r2["memory_id"]).category == "🦋 СДВГ"


@pytest.mark.asyncio
async def test_we_goes_to_misc():
    _me()
    r = await _store("у нас дома всегда тихо", _reply("дома всегда тихо", "🏠 Быт"))
    assert r["kind"] == "memory" and _row(r["memory_id"]).person_id is None


@pytest.mark.asyncio
async def test_single_known_person_gets_the_fact():
    pid = _person("Оля Иванова", ["Оля"])
    r = await _store("запомни, что Оля не ест мясо", _reply("Оля не ест мясо", "👥 Люди", "Оля", "оля_диета"))
    assert r["kind"] == "memory" and _row(r["memory_id"]).person_id == pid


@pytest.mark.asyncio
async def test_homonyms_ask_with_labels_and_write_nothing():
    a = _person("Миша Ари", ["Миша"], relation="friend", about="коллега по проекту")
    b = _person("Миша Бе", ["Миша"], relation="family")
    before = _count()
    r = await _store("Миша любит чай", _reply("Миша любит чай", "👥 Люди", "Миша", "миша_чай"))
    assert r["kind"] == "choose_person" and _count() == before
    labels = {c["id"]: c["label"] for c in r["ask"]["candidates"]}
    assert labels[a] == "Миша Ари — друг, коллега по проекту" and labels[b] == "Миша Бе — семья"
    res = await mem.finish_route(MARK, r["ask"], {"person_id": b})
    assert res["kind"] == "memory" and _row(res["memory_id"]).person_id == b


@pytest.mark.asyncio
async def test_choice_outside_candidates_is_refused():
    _person("Миша Ари", ["Миша"])
    _person("Миша Бе", ["Миша"])
    stranger = _person("Гоша", ["Гоша"])
    r = await _store("Миша любит чай", _reply("Миша любит чай", "👥 Люди", "Миша", "миша_чай"))
    assert (await mem.finish_route(MARK, r["ask"], {"person_id": stranger}))["kind"] == "error"
    assert _count() == 0


@pytest.mark.asyncio
async def test_unknown_person_asks_then_creates_card():
    before = _count()
    r = await _store("Даша боится высоты", _reply("Даша боится высоты", "👥 Люди", "Даша", "даша_страх"))
    assert r["kind"] == "ask_person" and r["ask"]["name"] == "Даша" and _count() == before
    res = await mem.finish_route(MARK, r["ask"], {"create": True, "relation": "friend"})
    assert res["kind"] == "memory" and res["person_name"] == "Даша"
    row = _row(res["memory_id"])
    with get_engine().connect() as c:
        card = c.execute(text("SELECT name, status, relation FROM people WHERE id = :i"), {"i": row.person_id}).one()
    assert card.name == "Даша" and card.status == "confirmed" and card.relation == "friend"


@pytest.mark.asyncio
async def test_unknown_person_no_goes_to_misc_without_card():
    r = await _store("Даша боится высоты", _reply("Даша боится высоты", "👥 Люди", "Даша", "даша_страх"))
    res = await mem.finish_route(MARK, r["ask"], {"misc": True})
    assert res["kind"] == "memory" and _row(res["memory_id"]).person_id is None
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM people WHERE user_id = :u"), {"u": MARK}).scalar() == 0


@pytest.mark.asyncio
async def test_plain_fact_still_goes_to_misc():
    _me()
    r = await _store("батон весит 4 кг", _reply("батон весит 4 кг", "🏠 Быт", "батон", "батон"))
    assert r["kind"] == "memory" and _row(r["memory_id"]).person_id is None


@pytest.mark.asyncio
async def test_budget_path_untouched():
    _me()
    r = await _store("лимит на кафе 5000р", _reply("лимит: 🍱 Кафе/Доставка — 5000₽/мес", "💰 Лимит", "кафе", "лимит_кафе"))
    assert r["kind"] == "memory" and _row(r["memory_id"]).person_id is None and _row(r["memory_id"]).category == "💰 Лимит"


# ── диалог в боте (pending_kv + кнопки) ────────────────────────────────────

class _Call:
    def __init__(self, data, uid=910001):
        from types import SimpleNamespace
        self.data = data
        self.from_user = SimpleNamespace(id=uid)
        self.message = SimpleNamespace(edit_text=AsyncMock())
        self.answer = AsyncMock()


@pytest.mark.asyncio
async def test_bot_dialog_yes_creates_card_then_relation_chip():
    from core import memory_route_ui as ui, pending_kv
    r = await _store("Даша боится высоты", _reply("Даша боится высоты", "👥 Люди", "Даша", "даша_страх"))
    pending_kv.save(910001, ui.KIND, {**r["ask"], "user_id": MARK})
    c1 = _Call("mr:yes")
    await ui.on_callback(c1)
    assert "Кто это?" in c1.message.edit_text.await_args.args[0]
    c2 = _Call("mr:r:friend")
    await ui.on_callback(c2)
    with get_engine().connect() as c:
        assert c.execute(text("SELECT relation FROM people WHERE user_id=:u AND name='Даша'"), {"u": MARK}).scalar() == "friend"
    assert pending_kv.get(910001, ui.KIND) is None


@pytest.mark.asyncio
async def test_bot_dialog_no_goes_to_misc_and_expired_session_is_told():
    from core import memory_route_ui as ui, pending_kv
    r = await _store("Даша боится высоты", _reply("Даша боится высоты", "👥 Люди", "Даша", "даша_страх"))
    pending_kv.save(910002, ui.KIND, {**r["ask"], "user_id": MARK})
    c = _Call("mr:no", uid=910002)
    await ui.on_callback(c)
    assert "Разное" in c.message.edit_text.await_args.args[0]
    gone = _Call("mr:no", uid=910003)
    await ui.on_callback(gone)
    assert "истекла" in gone.message.edit_text.await_args.args[0]
