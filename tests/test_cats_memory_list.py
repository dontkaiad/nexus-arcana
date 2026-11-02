"""tests/test_cats_memory_list.py — Cats phase 2, step 5: общий список Памяти не показывает факты, привязанные к коту
(они живут в разделе «Коты»), но факты категории «Коты» без кота остаются; поиск находит все.

Реальная тестовая БД, TestClient. Имена вымышленные.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert

from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cats-p2-memlist"
TG = 700000066
CATEGORY = "🐾 Коты"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(cats).where(cats.c.user_id == MARK))


@pytest.fixture
def api():
    app.dependency_overrides[current_user_id] = lambda: TG
    with patch("miniapp.backend.routes.memory.get_user_id", AsyncMock(return_value=MARK)):
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


def mk(text_, cat_id=None, category=CATEGORY, key="k"):
    with get_engine().begin() as c:
        return str(c.execute(insert(memories).values(
            fact_text=text_, key_name=key + text_[:4], category=category, related_to="", user_id=MARK,
            review_status="confirmed", cat_id=cat_id).returning(memories.c.id)).scalar_one())


def ids(resp):
    return {i["id"] for i in resp.json()["items"]}


def test_list_hides_cat_facts_keeps_general_and_other(api):
    cid = crepo.create(MARK, "Тучка")
    bound = mk("зюзябрикс Тучка любит окно", cat_id=cid)
    general = mk("зюзябрикс кошки любят тепло")
    other = mk("зюзябрикс обычная запись", category="🏠 Быт")
    got = ids(api.get("/api/memory"))
    assert bound not in got
    assert general in got and other in got


def test_search_still_finds_cat_facts(api):
    cid = crepo.create(MARK, "Тучка")
    bound = mk("зюзябрикс Тучка любит окно", cat_id=cid)
    with patch("core.memory_search._vector_leg", return_value=[]):
        found = ids(api.get("/api/memory", params={"q": "зюзябрикс"}))
    assert bound in found


def test_category_filter_does_not_resurrect_cat_facts(api):
    cid = crepo.create(MARK, "Тучка")
    bound = mk("зюзябрикс Тучка любит окно", cat_id=cid)
    general = mk("зюзябрикс общий факт")
    got = ids(api.get("/api/memory", params={"cat": CATEGORY}))
    assert general in got and bound not in got
