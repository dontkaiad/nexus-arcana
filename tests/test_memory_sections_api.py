"""tests/test_memory_sections_api.py — разделы Памяти: счётчики, раздел «Я», смена категории факта (тап и пакетно)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from core.db import get_engine
from core.repos.memories_table import memories
from core.repos.people_tables import people
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from tests._people_helpers import insert_person
from tests.test_people_api import mk_fact

MARK = "test-memory-sections"
TG = 700000077


@pytest.fixture
def client():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.people.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.memory.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            with get_engine().begin() as c:
                c.execute(delete(memories).where(memories.c.user_id == MARK))
                c.execute(delete(people).where(people.c.user_id == MARK))


def _cat(fid, cat):
    with get_engine().begin() as c:
        c.execute(text("UPDATE memories SET category = :c WHERE id = :i"), {"c": cat, "i": fid})


def _category(fid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT category FROM memories WHERE id = :i"), {"i": fid}).scalar()


def test_sections_counts(client):
    me = insert_person(MARK, "Я", is_self=True, status="confirmed")
    a = mk_fact(me, text_="любит чай", user=MARK, status="pending")
    b = mk_fact(me, text_="спорный факт", user=MARK, status="pending")
    r_ = mk_fact(me, text_="отклонён", user=MARK, status="rejected")
    _cat(a, "🛒 Предпочтения")
    _cat(b, "❓ Проверить автора")
    insert_person(MARK, "Оля", status="confirmed")
    insert_person(MARK, "Новая", status="pending")
    insert_person(MARK, "Плохая", status="rejected")
    r = client.get("/api/memory/sections").json()
    assert r["me"] == {"id": me, "total": 2, "pending": 2, "review": 1}
    assert r["people"] == {"total": 2, "pending": 1} and "total" in r["cats"] and "total" in r["misc"]


def test_self_section_lists_only_my_facts_and_filters_category(client):
    me = insert_person(MARK, "Я", is_self=True, status="confirmed")
    other = insert_person(MARK, "Оля", status="confirmed")
    a = mk_fact(me, text_="любит чай", user=MARK)
    b = mk_fact(me, text_="гипермобильность", user=MARK)
    mk_fact(other, text_="не моё", user=MARK)
    _cat(a, "🛒 Предпочтения")
    _cat(b, "🏥 Здоровье")
    r = client.get("/api/memory", params={"section": "me"}).json()
    assert sorted(i["text"] for i in r["items"]) == ["гипермобильность", "любит чай"]
    assert r["categories"] == ["🏥 Здоровье", "🛒 Предпочтения"]
    one = client.get("/api/memory", params={"section": "me", "cat": "🏥 Здоровье"}).json()
    assert [i["text"] for i in one["items"]] == ["гипермобильность"]


def test_change_category_one_and_many(client):
    me = insert_person(MARK, "Я", is_self=True, status="confirmed")
    a, b, c = (mk_fact(me, text_=t, user=MARK, status="pending") for t in ("а", "б", "в"))
    for f in (a, b, c):
        _cat(f, "🦋 СДВГ")
    assert client.post(f"/api/people/{me}/facts/category", json={"ids": [a], "category": "🏥 Здоровье"}).json()["moved"] == 1
    assert client.post(f"/api/people/{me}/facts/category", json={"ids": [b, c], "category": "🗂 Прочее"}).json()["moved"] == 2
    assert [_category(f) for f in (a, b, c)] == ["🏥 Здоровье", "🗂 Прочее", "🗂 Прочее"]
    with get_engine().connect() as cn:                                   # разбор (review_status) не тронут
        assert cn.execute(text("SELECT review_status FROM memories WHERE id = :i"), {"i": a}).scalar() == "pending"
    card = client.get(f"/api/people/{me}").json()
    assert {c_["name"]: c_["count"] for c_ in card["categories"]} == {"🏥 Здоровье": 1, "🗂 Прочее": 2}


def test_change_category_validation(client):
    me = insert_person(MARK, "Я", is_self=True, status="confirmed")
    other = insert_person(MARK, "Оля", status="confirmed")
    f = mk_fact(me, text_="а", user=MARK)
    g = mk_fact(other, text_="б", user=MARK)
    assert client.post(f"/api/people/{me}/facts/category", json={"ids": [f], "category": "выдумка"}).status_code == 400
    assert client.post(f"/api/people/{other}/facts/category", json={"ids": [g], "category": "🗂 Прочее"}).status_code == 400   # не «Я»
    # чужой факт через ручку «Я» не сдвинется
    assert client.post(f"/api/people/{me}/facts/category", json={"ids": [g], "category": "🗂 Прочее"}).json()["moved"] == 0
