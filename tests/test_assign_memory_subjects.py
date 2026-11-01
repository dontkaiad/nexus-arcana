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
])
def test_keyword_category(fact, cat):
    assert ms.keyword_category(fact) == cat


def test_keyword_category_leaves_ambiguous_to_haiku():
    assert ms.keyword_category("играет на гитаре") is None          # «игра» двусмысленно — не правило
    assert ms.keyword_category("красный цвет") is None


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


def test_parse_categorize_reply_falls_back():
    out = ms.parse_categorize_reply('{"1":"🏥 Здоровье","2":"выдумка"}', [1, 2, 3])
    assert out == {1: "🏥 Здоровье", 2: ms.FALLBACK_CATEGORY, 3: ms.FALLBACK_CATEGORY}
    assert ms.parse_categorize_reply("не json", [1]) == {1: ms.FALLBACK_CATEGORY}


# ── скрипт на БД ─────────────────────────────────────────────────────────────

@pytest.fixture
def world(tmp_path):
    sc = _script()
    sc.PLAN, sc.SAMPLE = str(tmp_path / "plan.json"), str(tmp_path / "sample.md")
    me = prepo.create(MARK, "Кай", ["каюшка"], is_self=True, status="confirmed")
    friend = prepo.create(MARK, "Оля", [], status="confirmed")

    def mem(fact, cat, key="k", person=None, status="confirmed", related=""):
        with get_engine().begin() as c:
            return c.execute(insert(memories).values(
                fact_text=fact, key_name=key, category=cat, related_to=related, person_id=person,
                review_status=status, user_id=MARK, source="auto").returning(memories.c.id)).scalar_one()

    w = {"sc": sc, "me": me, "friend": friend, "mem": mem}
    try:
        yield w
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())      # plan() делает asyncio.run → сбрасывает loop для тестов ниже
        with get_engine().begin() as c:
            c.execute(text("DROP TABLE IF EXISTS memories_subject_bak"))
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
        sc.plan()
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
        sc.plan()
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
