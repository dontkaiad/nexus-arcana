"""tests/test_work_relations.py — авто-привязка работа↔ритуал/расклад."""
from unittest.mock import AsyncMock, patch

import pytest

from core.work_relation import (
    _RELATION_FIELD_CACHE,
    attach_event_to_work,
    close_work_as_done,
    find_active_work_for_client,
    find_relation_field_to_works,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _RELATION_FIELD_CACHE.clear()
    yield
    _RELATION_FIELD_CACHE.clear()


@pytest.mark.asyncio
async def test_find_relation_field_resolves_via_schema():
    """Имя relation-поля резолвится из schema (любое — бот не знает заранее)."""
    schema = {
        "properties": {
            "Заметки": {"type": "rich_text"},
            "Ритуалы": {  # имя кривое, но указывает на 🔮 Работы
                "type": "relation",
                "relation": {"database_id": "works-db-id-fake"},
            },
        }
    }

    class _Fake:
        async def retrieve(self, database_id):
            return schema

    class _N:
        databases = _Fake()

    with patch("core.work_relation.get_notion", return_value=_N()):
        # config.arcana.db_works должен совпадать с relation target.
        with patch("core.work_relation.config") as cfg:
            cfg.arcana.db_works = "works-db-id-fake"
            field = await find_relation_field_to_works("ritual-db")
    assert field == "Ритуалы"


@pytest.mark.asyncio
async def test_find_relation_returns_none_if_no_works_relation():
    schema = {"properties": {"Заметки": {"type": "rich_text"}}}

    class _Fake:
        async def retrieve(self, database_id):
            return schema

    class _N:
        databases = _Fake()

    with patch("core.work_relation.get_notion", return_value=_N()):
        with patch("core.work_relation.config") as cfg:
            cfg.arcana.db_works = "works-db-id-fake"
            field = await find_relation_field_to_works("ritual-db")
    assert field is None


@pytest.mark.asyncio
async def test_find_active_work_returns_first():
    """find_active_work_for_client возвращает page_id первой совпадающей."""
    pages = [{"id": "w1"}, {"id": "w2"}]
    with patch(
        "core.work_relation.query_pages", new=AsyncMock(return_value=pages)
    ), patch("core.work_relation.config") as cfg:
        cfg.arcana.db_works = "works-db"
        wid = await find_active_work_for_client("c1", "✨ Ритуал", "u1")
    assert wid == "w1"


@pytest.mark.asyncio
async def test_find_active_work_empty_returns_none():
    with patch(
        "core.work_relation.query_pages", new=AsyncMock(return_value=[])
    ), patch("core.work_relation.config") as cfg:
        cfg.arcana.db_works = "works-db"
        wid = await find_active_work_for_client("c1", "✨ Ритуал", "u1")
    assert wid is None


@pytest.mark.asyncio
async def test_attach_event_to_work_uses_resolved_field():
    captured: dict = {}

    async def fake_update_page(page_id, props):
        captured["page_id"] = page_id
        captured["props"] = props

    async def fake_find_field(db_id):
        return "Ритуалы"

    with patch("core.work_relation.update_page", new=fake_update_page), \
         patch("core.work_relation.find_relation_field_to_works", new=fake_find_field):
        ok = await attach_event_to_work(
            event_db_id="ritual-db", event_page_id="r1", work_page_id="w1",
        )
    assert ok is True
    assert captured["props"] == {"Ритуалы": {"relation": [{"id": "w1"}]}}


@pytest.mark.asyncio
async def test_close_work_as_done():
    captured: dict = {}

    async def fake_update_page(page_id, props):
        captured["page_id"] = page_id
        captured["props"] = props

    with patch("core.work_relation.update_page", new=fake_update_page):
        ok = await close_work_as_done("w1")
    assert ok is True
    assert captured["page_id"] == "w1"
    assert captured["props"]["Status"]["status"]["name"] == "Done"
