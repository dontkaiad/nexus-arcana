"""tests/test_assign_memory_subjects.py — разметка Памяти по субъектам: правила (чистые) и скрипт plan/apply на БД.
Privacy: generic фикстуры."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete, insert, text

from core import memory_subjects as ms
from core.db import get_engine
from core.repos import pg_people_repo as prepo
from core.repos.memories_table import memories
from core.repos.people_tables import people

REPO = Path(__file__).resolve().parent.parent
MARK = "test-assign-subjects"


def _script():
    spec = importlib.util.spec_from_file_location("assign_memory_subjects", REPO / "scripts" / "assign_memory_subjects.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── чистые правила ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("fact,cat", [
    ("делает расклады на таро", "🔮 Практика"),
    ("аллергия на пыль", "🏥 Здоровье"),
    ("любит чай без сахара", "🛒 Предпочтения"),
    ("работает из дома", "💼 Работа"),
    ("гиперфокус мешает ложиться спать", "🦋 СДВГ"),
    ("обычно откладывает звонки", "🔄 Паттерн"),
    ("Кошка любит спать на подоконнике", "🐾 Коты"),
    ("Тучка не любит лоток", "🐾 Коты"),                          # имена котов — с заглавной
    ("сделала тату на плече", "🪞 Внешность"),
    ("у неё хорошие отношения с бывшим", "💞 Отношения"),
    ("мама живёт в другом городе", "💞 Отношения"),
    ("у неё мания величия", "🏥 Здоровье"),                       # мании/психиатрия — Здоровье, не СДВГ
    ("в день рождения идёт к лор-хирургу", "🏥 Здоровье"),
])
def test_keyword_category(fact, cat):
    assert ms.keyword_category(fact) == cat


def test_keyword_category_leaves_ambiguous_to_haiku():
    assert ms.keyword_category("играет на гитаре") is None          # «игра» двусмысленно — не правило
    assert ms.keyword_category("красный цвет") is None


def test_loves_depressive_is_a_taste_not_health():
    assert ms.keyword_category("обожает депрессивного футболиста") == "🛒 Предпочтения"
    assert ms.keyword_category("у неё депрессия") == "🏥 Здоровье"


def test_ritual_needs_esoteric_context():
    assert ms.keyword_category("не любит любовные ритуалы") == "🛒 Предпочтения"     # сам по себе «ритуал» ничего не значит
    assert ms.keyword_category("делает ритуал на удачу со свечой") == "🔮 Практика"
    assert ms.keyword_category("нравится лор игры") == "🎮 Хобби"                      # «лор» сам не ключевое слово: не Здоровье


def test_lowercase_cat_name_is_not_a_cat():
    assert ms.keyword_category("любит смотреть на тучку", ["Тучка"]) != "🐾 Коты"      # слово с маленькой — облачко, не кличка


@pytest.mark.parametrize("fact,quote,author,verdict,why", [
    ("любит чай", "я обожаю чай", "kai", "keep", "kai_first"),
    ("купил машину", "у меня теперь есть машина", "kai", "keep", "kai_first"),          # факт в м.р., но цитата Кай от первого лица
    ("любит чай", "люблю чай", "kai", "keep", "kai_first"),                              # 1 л. по глаголу, без «я»
    ("ненавидит жару", "ненавижу жару", "kai", "keep", "kai_first"),
    ("жила в городе", "жил в городе раньше", "kai", "keep", "kai_first"),                # прош. вр. в любом роде
    ("жила в городе", "жила в городе раньше", "kai", "keep", "kai_first"),
    ("любит кофе", "ты любишь кофе", "kai", "to_chat", "kai_about_other"),               # Кай про собеседника → на человека чата
    ("любит руки", "Я обожаю твои руки", "partner", "check", "partner_both_persons"),    # оба лица → не угадываем
    ("любит тебя", "люблю тебя за честность", "kai", "check", "kai_both_persons"),
    ("бесит", "тебя бесит шум", "kai", "to_chat", "kai_about_other"),
    ("любит твои руки", "твои руки классные", "kai", "check", "kai_second_object"),
    ("работает дома", "ты работаешь дома", "partner", "keep", "partner_about_me"),
    ("устаёт от небритости", "я через неделю устаю от небритости", "partner", "to_chat", "partner_first"),
    ("не ест мясо", "не ем мясо", "partner", "to_chat", "partner_first"),
    ("уехал", "он уехал", "partner", "check", "partner_no_marker"),
    ("уехал", "он уехал", "kai", "check", "kai_no_marker"),                              # другое подлежащее: прош. вр. не считается 1 л.
    ("любит кино", "я люблю кино", "mixed", "check", "author_mixed"),
    ("любит кино", "я люблю кино", None, "check", "author_unknown"),
    ("Собеседник любит чай", "я обожаю чай", "kai", "interloc", "собеседник в тексте"),   # «Собеседник» важнее цитаты
    ("любит чай", "", "kai", "check", "kai_no_marker"),             # пустая цитата — доказательств нет
])
def test_attribution_by_quote(fact, quote, author, verdict, why):
    assert ms.attribution(fact, quote, author) == (verdict, why)


def test_both_persons_always_check():
    q = "люблю маму, а ты любишь папу"
    for fact in ("любит маму", "любит папу"):
        for author in ("kai", "partner"):
            assert ms.attribution(fact, q, author)[0] == "check"


def test_elided_kai_fragment_stays_unless_other_person_named():
    assert ms.attribution("не любит сигареты", "сигареты фу", "kai") == ("keep", "kai_elided")
    assert ms.attribution("х", "бесит идти рядом с ним", "kai") == ("check", "kai_no_marker")   # «ним» — другой человек
    assert ms.attribution("х", "надо было отобрать у Лехи", "kai") == ("check", "kai_no_marker")
    assert ms.attribution("х", "сигареты фу", "partner") == ("check", "partner_no_marker")   # у партнёра обрывок не берём


def test_gender_is_not_used():
    # род в факте и в цитате ничего не значит: решает лицо
    assert ms.attribution("купил машину", "купила машину", "kai") == ("keep", "kai_first")
    assert ms.attribution("купила машину", "купил машину", "kai") == ("keep", "kai_first")


def test_cat_names_only_as_nickname():
    names = ["Тучка", "Туча"]
    assert ms.keyword_category("Тучка не любит воду", names) == "🐾 Коты"
    assert ms.keyword_category("Туча не любит воду", names) == "🐾 Коты"                   # кличка из таблицы
    assert ms.keyword_category("Тучка Юниверс любимый мультик", names) != "🐾 Коты"
    assert ms.keyword_category("не нравится мультик «Тучка Юниверс»", names) == "🎮 Хобби"
    assert ms.keyword_category("Илюша спит", names) != "🐾 Коты"


def test_cat_names_are_not_hardcoded_without_names_only_cat_words_work():
    assert ms.keyword_category("Тучка не любит воду") != "🐾 Коты"                         # имён нет → по имени не определить
    assert ms.keyword_category("Тучка не любит воду", []) != "🐾 Коты"
    assert ms.keyword_category("Тучка не любит лоток") == "🐾 Коты"                        # слова «лоток», «кот» работают без имён
    assert ms.keyword_category("кот боится пылесоса") == "🐾 Коты"


def test_cat_names_regex_escapes_special_characters():
    assert ms.keyword_category("Тучка (мини) спит", ["Тучка (мини)"]) == "🐾 Коты"
    assert ms.keyword_category("Тучка+ спит", ["Тучка+"]) == "🐾 Коты"


def test_new_category_rules():
    assert ms.keyword_category("играет в LoL по вечерам") == "🎮 Хобби"
    assert ms.keyword_category("пишет фанфики по аниме") == "🎮 Хобби"
    assert ms.keyword_category("рисует косплей") == "🎮 Хобби"
    assert ms.keyword_category("занимается эзотерикой") == "🔮 Практика"
    assert ms.keyword_category("обожает дворы и колодцы") != "🔮 Практика"          # «колод» — не эзотерика
    assert ms.keyword_category("считает важным иметь официальный статус отношений") == "💞 Отношения"   # не «тату» в «статус»
    assert ms.keyword_category("любит гладить волосы") == "🛒 Предпочтения"          # тактильное, не внешность
    assert ms.keyword_category("красит волосы в синий") == "🪞 Внешность"


def test_temp_signal():
    assert ms.temp_signal("сейчас находится в Москве")
    assert ms.temp_signal("яйца в холодильнике")
    assert not ms.temp_signal("любит чай")


def test_adhd_rule_not_triggered_by_substring_noise():
    # раньше «рас» ловило «расстроен» / «расклад» → ложный СДВГ
    assert ms.keyword_category("расстроен из-за погоды") != "🦋 СДВГ"


@pytest.mark.parametrize("cat,key,pids,want", [
    ("💰 Лимит", "лимит_кафе", [], "system"),
    ("🛒 Предпочтения", "постоянно_вода", [], "system"),        # по ключу, даже при чужой категории
    ("🦋 СДВГ", "триггер_шум", [], "self"),
    ("🐾 Коты", "кот_еда", [], "cats"),
    ("👥 Люди", "маша", [5], "person"),
    ("👥 Люди", "миша", [5, 6], "misc"),                         # тёзки — без догадок
    ("👥 Люди", "кто-то", [], "misc"),
    ("🏠 Быт", "батон", [], "misc"),
])
def test_legacy_target(cat, key, pids, want):
    assert ms.legacy_target(cat, key, pids) == want


def test_parse_categorize_reply_new_categories():
    out = ms.parse_categorize_reply('{"1":"Коты","2":"внешность","3":"Отношения"}', [1, 2, 3])
    assert out == {1: "🐾 Коты", 2: "🪞 Внешность", 3: "💞 Отношения"}


def test_haiku_cannot_return_service_category():
    assert ms.parse_categorize_reply('{"1":"Проверить автора"}', [1]) == {1: ms.FALLBACK_CATEGORY}


def test_parse_categorize_reply_falls_back():
    out = ms.parse_categorize_reply('{"1":"🏥 Здоровье","2":"выдумка"}', [1, 2, 3])
    assert out == {1: "🏥 Здоровье", 2: ms.FALLBACK_CATEGORY, 3: ms.FALLBACK_CATEGORY}
    assert ms.parse_categorize_reply("не json", [1]) == {1: ms.FALLBACK_CATEGORY}


def test_parse_categorize_reply_accepts_names_without_emoji():
    # на пачке из 50 фактов модель опускает эмодзи — раньше это превращало весь остаток в «Прочее»
    out = ms.parse_categorize_reply('{"1":"Здоровье","2":"сдвг","3":"🛒Предпочтения","4":null}', [1, 2, 3, 4])
    assert out == {1: "🏥 Здоровье", 2: "🦋 СДВГ", 3: "🛒 Предпочтения", 4: ms.FALLBACK_CATEGORY}


# ── скрипт на БД ─────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path):
    sc = _script()
    sc.PLAN, sc.SAMPLE = str(tmp_path / "plan.json"), str(tmp_path / "sample.md")
    me = prepo.create(MARK, "Кай", ["каюшка"], is_self=True, status="confirmed")
    friend = prepo.create(MARK, "Оля", [], status="confirmed")

    def mem(fact, cat, key="k", person=None, status="confirmed", related="", chat=None,
            quote="я так считаю", author="kai"):
        with get_engine().begin() as c:
            return c.execute(insert(memories).values(
                fact_text=fact, key_name=key, category=cat, related_to=related, person_id=person,
                review_status=status, user_id=MARK, source="auto", source_chat_id=chat,
                source_quote=quote, quote_author=author).returning(memories.c.id)).scalar_one()

    w = {"sc": sc, "me": me, "friend": friend, "mem": mem}
    try:
        yield w
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())      # plan() делает asyncio.run → сбрасывает loop для тестов ниже
        with get_engine().begin() as c:
            c.execute(text("DROP TABLE IF EXISTS memories_subject_bak"))
            c.execute(text("DROP TABLE IF EXISTS memories_deleted_bak"))
            c.execute(delete(memories).where(memories.c.user_id == MARK))
            c.execute(delete(people).where(people.c.user_id == MARK))


def _haiku_stub(answer):
    async def fake(rows, usage):
        usage["in"], usage["out"] = 100, 10
        return {r.id: answer for r in rows}
    return fake


def test_plan_then_apply_keeps_review_status(world):
    sc, me, mem = world["sc"], world["me"], world["mem"]
    a = mem("аллергия на пыль", "👥 Люди", f"person_{me}_important_aaaaaaaa", me, "pending", "Кай")
    b = mem("играет на гитаре", "👥 Люди", f"person_{me}_other_bbbbbbbb", me, "pending", "Кай")
    old_self = mem("диета без глютена", "🦋 СДВГ", "диета", None, "confirmed")
    old_friend = mem("Оля любит чай", "👥 Люди", "оля", None, "confirmed", "Оля")
    old_misc = mem("батон весит 4 кг", "🏠 Быт", "батон", None, "confirmed")
    old_sys = mem("лимит: кафе", "💰 Лимит", "лимит_кафе", None, "confirmed")

    with patch.object(sc, "_haiku", _haiku_stub("🏠 Быт")):
        sc.plan("", "", "all")
    plan = json.loads(Path(sc.PLAN).read_text())
    assert {i["id"]: (i["cat"], i["by"]) for i in plan["self"]} == {a: ("🏥 Здоровье", "kw"), b: ("🏠 Быт", "haiku")}
    mine = {old_self, old_friend, old_misc, old_sys}                 # в тестовой БД могут лежать чужие строки
    assert {i["id"]: i["to"] for i in plan["legacy"] if i["id"] in mine} == {
        old_self: "self", old_friend: "person", old_misc: "misc", old_sys: "system"}
    sample = Path(sc.SAMPLE).read_text()
    assert "аллергия на пыль" in sample and "играет на гитаре" in sample and "батон весит" in sample
    assert "лимит: кафе" not in sample                              # бюджет в выборку не идёт

    with get_engine().connect() as c:                                # dry-run ничего не записал
        assert c.execute(text("SELECT category FROM memories WHERE id=:i"), {"i": a}).scalar() == "👥 Люди"

    sc.apply()
    with get_engine().connect() as c:
        rows = {r.id: r for r in c.execute(text(
            "SELECT id, category, person_id, review_status, key_name, related_to FROM memories WHERE user_id=:u"), {"u": MARK})}
    assert rows[a].category == "🏥 Здоровье" and rows[a].review_status == "pending"
    assert rows[b].category == "🏠 Быт" and rows[b].review_status == "pending"
    assert rows[old_self].person_id == me and rows[old_self].category == "🦋 СДВГ"
    assert rows[old_self].key_name.startswith(f"person_{me}_") and rows[old_self].review_status == "confirmed"
    assert rows[old_self].related_to.startswith("Кай")
    assert rows[old_friend].person_id == world["friend"]
    assert rows[old_misc].person_id is None and rows[old_sys].person_id is None
    with get_engine().connect() as c:                                # бэкап есть и откатывает
        assert c.execute(text("SELECT count(*) FROM memories_subject_bak WHERE id = ANY(:i)"),
                         {"i": [a, b, old_self, old_friend]}).scalar() == 4


def test_apply_skips_rows_changed_since_plan(world):
    sc, me, mem = world["sc"], world["me"], world["mem"]
    a = mem("аллергия на пыль", "👥 Люди", f"person_{me}_important_aaaaaaaa", me, "pending")
    with patch.object(sc, "_haiku", _haiku_stub("🗂 Прочее")):
        sc.plan("", "", "all")
    with get_engine().begin() as c:                                  # владелец успела вручную разнести в СДВГ
        c.execute(text("UPDATE memories SET category='🦋 СДВГ' WHERE id=:i"), {"i": a})
    sc.apply()
    with get_engine().connect() as c:
        assert c.execute(text("SELECT category FROM memories WHERE id=:i"), {"i": a}).scalar() == "🦋 СДВГ"


def test_apply_rejects_unknown_category(world):
    sc = world["sc"]
    Path(sc.PLAN).write_text(json.dumps({"self_id": world["me"], "self": [{"id": 1, "cat": "💥", "by": "kw"}], "legacy": []}))
    with pytest.raises(SystemExit):
        sc.apply()


def test_attribution_by_quote_end_to_end(world):
    sc, me, mem = world["sc"], world["me"], world["mem"]
    friend = world["friend"]
    mem("любит чай", "👥 Люди", f"person_{friend}_likes_11111111", friend, "confirmed", chat=-501)       # чат 501 → только Оля
    P = "👥 Люди"
    mine = mem("купил машину", P, f"person_{me}_other_aaaaaaa1", me, "pending", "Кай", chat=-501,
               quote="я купила машину", author="kai")                       # текст факта в м.р., но цитата Кай → остаётся
    about_me = mem("любит руки", P, f"person_{me}_other_aaaaaaa2", me, "pending", "Кай", chat=-501,
                   quote="ты обожаешь руки", author="partner")           # «ты» → обо мне со слов собеседника
    mapped = mem("устаёт от жары", P, f"person_{me}_other_aaaaaaa3", me, "pending", "Кай", chat=-501,
                 quote="я устаю от жары", author="partner")                  # партнёр о себе, чат сопоставлен → на Олю
    nochat = mem("устаёт от шума", P, f"person_{me}_other_aaaaaaa4", me, "pending", "Кай", chat=-999,
                 quote="я устаю от шума", author="partner")                  # чат не сопоставлен → ❓
    nomark = mem("любит кино", P, f"person_{me}_other_aaaaaaa5", me, "pending", "Кай", chat=-501,
                 quote="он любит кино", author="kai")                      # без маркера → ❓
    other = mem("любит острое", P, f"person_{me}_other_aaaaaab1", me, "pending", "Кай", chat=-501,
                quote="ты любишь острое", author="kai")                    # Кай про собеседника → на Олю (чат сопоставлен)
    interloc = mem("Собеседник любит чай", P, f"person_{me}_other_aaaaaaa6", me, "pending", "Кай", chat=-501,
                   quote="я люблю чай", author="kai")
    temp = mem("сейчас находится в отпуске", P, f"person_{me}_other_aaaaaaa7", me, "pending", "Кай", chat=-501,
               quote="я сейчас в отпуске", author="kai")
    kept = mem("сейчас любит кофе", P, f"person_{me}_other_aaaaaaa8", me, "pending", "Кай", chat=-501,
               quote="я сейчас люблю кофе", author="kai")

    with patch.object(sc, "_haiku", side_effect=AssertionError("атрибуция идёт БЕЗ LLM")):
        sc.plan("", f"{kept}")                                               # этап по умолчанию — attribution
    plan = json.loads(Path(sc.PLAN).read_text())
    g = {x["id"]: x for x in plan["gender"]}
    assert g[mapped]["action"] == "reassign" and g[mapped]["person_id"] == friend
    assert g[other]["action"] == "reassign" and g[other]["person_id"] == friend
    assert g[nochat]["action"] == "check" and "не сопоставлен" in g[nochat]["why"]
    assert g[nomark]["action"] == "check" and g[nomark]["why"] == "kai_no_marker"
    assert g[interloc]["action"] == "reject"
    assert mine not in g and about_me not in g
    assert plan["self"] == []                                                 # категорий на этом этапе нет
    assert temp in plan["temp"] and kept not in plan["temp"]
    sample = Path(sc.SAMPLE).read_text()
    assert "я устаю от жары" in sample and "kai_first" in sample and "[ОСТАЮТСЯ]" in sample

    sc.apply()
    with get_engine().connect() as c:
        rows = {r.id: r for r in c.execute(text(
            "SELECT id, person_id, review_status, key_name, category FROM memories WHERE user_id=:u"), {"u": MARK})}
    assert rows[other].person_id == friend
    assert rows[mapped].person_id == friend and rows[mapped].review_status == "pending"
    assert rows[nochat].person_id == me and rows[nochat].category == "❓ Проверить автора" and rows[nochat].review_status == "pending"
    assert rows[nomark].category == "❓ Проверить автора"
    assert rows[interloc].review_status == "rejected"
    for keep_id in (mine, about_me, kept):
        assert rows[keep_id].person_id == me and rows[keep_id].review_status == "pending" and rows[keep_id].category == P
    assert rows[temp].review_status == "rejected"


def test_manual_overrides_delete_misc_self_person(world):
    sc, me, mem = world["sc"], world["me"], world["mem"]
    ilya = prepo.create(MARK, "Илья", [], status="confirmed")
    junk = mem("в процессе", "🦋 СДВГ", "x1", None, "confirmed")
    misc = mem("Arcana — бот", "💡 Инсайт", "x2", None, "confirmed")
    to_self = mem("любит Вадима", "👥 Люди", "x3", None, "confirmed", "Вадим")
    card = mem("Илья друг, есть ключи", "👥 Люди", "x4", None, "confirmed")
    noname = mem("Дима живёт рядом", "👥 Люди", "x5", None, "confirmed")
    arg = f"{junk}:delete,{misc}:misc,{to_self}:self,{card}:person:Илья,{noname}:person:Никого"
    with patch.object(sc, "_haiku", _haiku_stub("🗂 Прочее")):
        sc.plan(arg)
    plan = {i["id"]: i for i in json.loads(Path(sc.PLAN).read_text())["legacy"]}
    assert [plan[junk]["to"], plan[misc]["to"], plan[to_self]["to"], plan[card]["to"]] == ["delete", "misc", "self", "person"]
    assert plan[card]["person_id"] == ilya and plan[noname]["to"] == "misc"       # карточки нет — оставляем как есть
    assert "карточка «Никого» не найдена" in Path(sc.SAMPLE).read_text()

    sc.apply()
    with get_engine().connect() as c:
        left = {r.id: r for r in c.execute(text("SELECT id, person_id FROM memories WHERE user_id=:u"), {"u": MARK})}
        assert junk not in left                                                     # удалено
        assert c.execute(text("SELECT count(*) FROM memories_deleted_bak WHERE id=:i"), {"i": junk}).scalar() == 1
    assert left[misc].person_id is None and left[to_self].person_id == me
    assert left[card].person_id == ilya and left[noname].person_id is None


def test_manual_birthday_moves_date_into_person_card(world):
    sc, me, mem = world["sc"], world["me"], world["mem"]
    anna = prepo.create(MARK, "Анна", [], status="confirmed")
    boris = prepo.create(MARK, "Борис", [], status="confirmed")
    with get_engine().begin() as c:
        c.execute(text("UPDATE people SET birthday='1990-05-18' WHERE id=:i"), {"i": boris})     # у Бориса дата уже есть, другая
    a = mem("Анна — подруга с синими волосами, день рождения 18 мая 1994 года", "👥 Люди", "d1", None, "confirmed", "Анна")
    b = mem("день рождения 17 октября 2004", "👥 Люди", "d2", None, "confirmed", "Анна")
    c_ = mem("день рождения 3 марта 1991", "👥 Люди", "d3", None, "confirmed", "Борис")
    unknown = mem("день рождения 1 января 2000", "👥 Люди", "d4", None, "confirmed", "Никто")
    with patch.object(sc, "_haiku", _haiku_stub("🗂 Прочее")):
        sc.plan(f"{a}:birthday,{b}:birthday,{c_}:birthday,{unknown}:birthday")
    plan = {i["id"]: i for i in json.loads(Path(sc.PLAN).read_text())["legacy"]}
    assert plan[a]["to"] == "birthday" and (plan[a]["month"], plan[a]["day"], plan[a]["year"]) == (5, 18, 1994)
    assert "подруга с синими волосами" in plan[a]["rest"] and not plan[b]["rest"]
    assert plan[unknown]["to"] == "misc" and "оставлено как есть" in Path(sc.SAMPLE).read_text()
    sc.apply()
    with get_engine().connect() as c:
        bd = c.execute(text("SELECT birthday FROM people WHERE id=:i"), {"i": anna}).scalar()
        left = {r.id: r.person_id for r in c.execute(text("SELECT id, person_id FROM memories WHERE user_id=:u"), {"u": MARK})}
        boris_bd = c.execute(text("SELECT birthday FROM people WHERE id=:i"), {"i": boris}).scalar()
    assert str(bd) == "1994-05-18"                                     # у Анны два ДР: первая запись выигрывает
    assert left[a] == anna                                             # остаток факта («подруга с синими волосами») сохранён
    assert left[b] is None                                             # вторая дата противоречит первой — не перезаписываем, факт остаётся
    assert str(boris_bd) == "1990-05-18" and left[c_] is None           # чужая дата не перезаписана, факт остался


# ── имена котов — из таблицы cats ────────────────────────────────────────────

CAT_MARK = "test-assign-subjects-cats"


@pytest.fixture
def cats_cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM cats WHERE user_id IN (:a, :b)"), {"a": MARK, "b": CAT_MARK})


def test_load_cat_names_from_table_per_owner(cats_cleanup):
    from core.repos import pg_cats_repo as crepo
    crepo.create(MARK, "Тучка", ["Туча"])
    crepo.create(MARK, "Пончик")
    gone = crepo.create(MARK, "Рыжик")
    crepo.update_cat(MARK, gone, {"status": "rejected"})
    crepo.create(CAT_MARK, "Чужекот")
    assert ms.load_cat_names(MARK) == ["Тучка", "Туча", "Пончик"]       # без убранных и без чужих
    assert ms.load_cat_names("test-assign-subjects-nobody") == []         # котов нет — пустой набор


def test_load_cat_names_never_raises():
    with patch("core.repos.pg_cats_repo.cat_names", side_effect=RuntimeError("db down")):
        assert ms.load_cat_names(MARK) == []
    with patch("core.repos.pg_cats_repo.cat_names", side_effect=Exception("no such table: cats")):
        assert ms.load_cat_names(MARK) == []


def test_plan_takes_cat_names_from_table(world, cats_cleanup):
    from core.repos import pg_cats_repo as crepo
    sc, me, mem = world["sc"], world["me"], world["mem"]
    crepo.create(MARK, "Рыжик", ["Рыжуля"])
    named = mem("Рыжик спит на диване", "👥 Люди", f"person_{me}_other_aaaaaaa1", me, "pending", "Кай")
    alias = mem("Рыжуля любит окно", "👥 Люди", f"person_{me}_other_aaaaaaa2", me, "pending", "Кай")
    other = mem("Барсик спит на диване", "👥 Люди", f"person_{me}_other_aaaaaaa3", me, "pending", "Кай")   # такого кота нет
    with patch.object(sc, "_haiku", _haiku_stub("🏠 Быт")):
        sc.plan("", "", "all")
    plan = json.loads(Path(sc.PLAN).read_text())
    got = {i["id"]: (i["cat"], i["by"]) for i in plan["self"]}
    assert got[named] == ("🐾 Коты", "kw") and got[alias] == ("🐾 Коты", "kw")
    assert got[other] == ("🏠 Быт", "haiku")                               # неизвестная кличка — решает Haiku, не правило


def test_plan_without_cats_table_rows_still_works(world):
    sc, me, mem = world["sc"], world["me"], world["mem"]
    x = mem("Рыжик спит на диване", "👥 Люди", f"person_{me}_other_bbbbbbb1", me, "pending", "Кай")
    with patch.object(sc, "_haiku", _haiku_stub("🏠 Быт")), patch("core.repos.pg_cats_repo.cat_names", side_effect=RuntimeError("x")):
        sc.plan("", "", "all")
    plan = json.loads(Path(sc.PLAN).read_text())
    assert {i["id"]: i["by"] for i in plan["self"]}[x] == "haiku"           # сбой таблицы cats не роняет разметку


def test_chat_people_matches_confirmed_and_pending_but_not_rejected():
    # регрессия v4: чат сопоставляется с человеком по фактам любого статуса, кроме rejected-людей и self
    import os
    import sqlalchemy as sa
    if not os.getenv("DATABASE_URL"):
        pytest.skip("нужна scratch-БД")
    from scripts.assign_memory_subjects import _chat_people
    from core.db import get_engine
    with get_engine().connect() as c:
        assert _chat_people(c, sa, {-1}, 0) == {}                   # чата без людей нет — пусто, а не ошибка
