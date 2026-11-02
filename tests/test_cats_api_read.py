"""tests/test_cats_api_read.py — Cats phase 2, step 2: GET /api/cats, /api/cats/{id}, /api/cats/general.

Реальная тестовая БД, TestClient, только владелец. Имена вымышленные; строки помечены MARK.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert

from core.cats import cat_fact_key
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cats-p2-read"
OTHER = "test-cats-p2-read-other"
TG = 700000088
CATEGORY = "🐾 Коты"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as conn:
        conn.execute(delete(memories).where(memories.c.user_id.in_([MARK, OTHER])))
        conn.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def api():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


def mk_fact(cat_id, text_, user=MARK, key=None, **kw):
    key = key if key is not None else (cat_fact_key(cat_id, "other", text_) if cat_id else "k_" + text_[:6])
    vals = dict(fact_text=text_, key_name=key, category=CATEGORY, related_to="", user_id=user,
                review_status="confirmed", cat_id=cat_id)
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(memories).values(**vals).returning(memories.c.id)).scalar_one()


# ── список ───────────────────────────────────────────────────────────────────

def test_list_returns_cats_with_counts_photo_and_general_count(api):
    a = crepo.create(MARK, "Тучка", ["Туча"], photo_url="https://example.invalid/t.jpg")
    b = crepo.create(MARK, "Пончик")
    mk_fact(a, "Тучка любит окно")
    mk_fact(a, "Тучка не ест курицу")
    mk_fact(None, "кот боится пылесоса")
    d = api.get("/api/cats").json()
    assert d["total"] == 2 and d["general_count"] == 1
    by = {c["id"]: c for c in d["cats"]}
    assert by[a] == {"id": a, "name": "Тучка", "aliases": ["Туча"], "photo_url": "https://example.invalid/t.jpg",
                     "status": "confirmed", "facts_count": 2}
    assert by[b]["photo_url"] is None and by[b]["facts_count"] == 0       # без фото → фронт покажет заглушку


def test_list_hides_rejected_and_other_users_cats(api):
    crepo.create(MARK, "Тучка")
    gone = crepo.create(MARK, "Пончик")
    crepo.update_cat(MARK, gone, {"status": "rejected"})
    crepo.create(OTHER, "Чужекот")
    names = [c["name"] for c in api.get("/api/cats").json()["cats"]]
    assert names == ["Тучка"]


def test_list_empty(api):
    assert api.get("/api/cats").json() == {"total": 0, "cats": [], "general_count": 0}


# ── карточка ─────────────────────────────────────────────────────────────────

def test_card_groups_facts_by_type_and_strips_name_prefix(api):
    cid = crepo.create(MARK, "Тучка")
    mk_fact(cid, "Тучка: любит окно", key=cat_fact_key(cid, "likes", "Тучка: любит окно"))   # префикс «Имя: » ставит парсер
    mk_fact(cid, "Тучка не ест курицу", key=cat_fact_key(cid, "dislikes", "Тучка не ест курицу"))
    mk_fact(cid, "аллергия на курицу", key=cat_fact_key(cid, "important", "аллергия на курицу"))
    mk_fact(cid, "спит на диване", key=cat_fact_key(cid, "other", "спит на диване"))
    d = api.get(f"/api/cats/{cid}").json()
    assert d["name"] == "Тучка" and d["facts_count"] == 4
    assert [g["type"] for g in d["facts"]] == ["important", "likes", "dislikes", "other"]
    texts = {g["type"]: g["items"][0]["text"] for g in d["facts"]}
    assert texts["likes"] == "любит окно"                       # префикс «Имя: » срезан
    assert texts["dislikes"] == "Тучка не ест курицу"           # имя внутри предложения не трогаем
    assert texts["important"] == "аллергия на курицу"


def test_card_reads_legacy_keys_by_text(api):
    cid = crepo.create(MARK, "Тучка")
    mk_fact(cid, "Тучка не ест курицу", key="тучка_еда")                  # старый ключ, не cat_<id>_…
    d = api.get(f"/api/cats/{cid}").json()
    assert [g["type"] for g in d["facts"]] == ["dislikes"]


def test_card_skips_archived_tombstone_rejected_and_other_cats_facts(api):
    cid = crepo.create(MARK, "Тучка")
    other = crepo.create(MARK, "Пончик")
    keep = mk_fact(cid, "Тучка любит окно")
    mk_fact(cid, "старое", is_archived=True)
    mk_fact(cid, "", key="tomb")
    mk_fact(cid, "отклонённый", review_status="rejected")
    mk_fact(other, "Пончик спит")
    d = api.get(f"/api/cats/{cid}").json()
    assert [i["id"] for g in d["facts"] for i in g["items"]] == [keep]


def test_card_keeps_inactive_fact_flagged(api):
    cid = crepo.create(MARK, "Тучка")
    mk_fact(cid, "Тучка любит окно", is_current=False)
    (item,) = api.get(f"/api/cats/{cid}").json()["facts"][0]["items"]
    assert item["is_current"] is False


def test_card_404_for_missing_rejected_and_foreign(api):
    mine = crepo.create(MARK, "Тучка")
    rej = crepo.create(MARK, "Пончик")
    crepo.update_cat(MARK, rej, {"status": "rejected"})
    foreign = crepo.create(OTHER, "Чужекот")
    assert api.get(f"/api/cats/{mine}").status_code == 200
    for cid in (rej, foreign, 987654321):
        assert api.get(f"/api/cats/{cid}").status_code == 404


# ── «Общее про котов» ────────────────────────────────────────────────────────

def test_general_lists_category_facts_without_cat_id(api):
    cid = crepo.create(MARK, "Тучка")
    g1 = mk_fact(None, "кот боится пылесоса", related_to="кот")
    g2 = mk_fact(None, "авиакомпания пускает животных до 23 кг", key="s_travel")
    mk_fact(cid, "Тучка любит окно")                                       # привязан — не общий
    mk_fact(None, "про другое", category="🏠 Быт")                         # не категория «Коты»
    mk_fact(None, "чужое", user=OTHER)
    mk_fact(None, "архив", is_archived=True)
    d = api.get("/api/cats/general").json()
    ids = {i["id"] for g in d["facts"] for i in g["items"]}
    assert ids == {g1, g2} and d["total"] == 2


def test_general_does_not_strip_text_and_route_not_shadowed(api):
    mk_fact(None, "Всем котам нужна вода")
    r = api.get("/api/cats/general")
    assert r.status_code == 200
    assert r.json()["facts"][0]["items"][0]["text"] == "Всем котам нужна вода"


# ── доступ ───────────────────────────────────────────────────────────────────

def test_no_owner_is_403():
    app.dependency_overrides[current_user_id] = lambda: TG
    try:
        with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=None)):
            c = TestClient(app)
            assert c.get("/api/cats").status_code == 403
            assert c.get("/api/cats/general").status_code == 403
            assert c.get("/api/cats/1").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_without_auth_is_rejected():
    app.dependency_overrides.clear()
    c = TestClient(app)
    for url in ("/api/cats", "/api/cats/general", "/api/cats/1"):
        assert c.get(url).status_code in (401, 403)
