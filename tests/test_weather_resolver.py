"""issue #70: смена локации должна обновлять город в Mini App.

Резолвер:
1. явный ключ `city_{tg_id}` побеждает fuzzy-скан;
2. fuzzy-скан грузит записи отсортированные last_edited_time desc — свежая
   запись побеждает старую.
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_explicit_city_key_overrides_fuzzy_scan():
    from miniapp.backend.routes import weather

    async def fake_memory_get(key: str):
        if key == "city_42":
            return "Питер"
        return None

    # query_pages не должен вызываться — override срабатывает раньше
    fetch = AsyncMock(side_effect=AssertionError("query_pages не должен вызываться при override"))

    with patch.object(weather, "memory_get", fake_memory_get), \
         patch.object(weather, "query_pages", fetch):
        city = await weather._resolve_city_from_memory(42)

    assert city == "Saint Petersburg"


@pytest.mark.asyncio
async def test_fuzzy_scan_sorted_by_last_edited_desc():
    """query_pages должен вызываться с sorts=last_edited_time desc."""
    from miniapp.backend.routes import weather

    async def empty_get(key: str):
        return None

    captured_kwargs: dict = {}

    async def fake_query(db_id, filters=None, sorts=None, page_size=20):
        captured_kwargs["sorts"] = sorts
        # отдаём «свежую» запись «Питер» первой, «Москва» второй
        return [
            {"properties": {
                "Текст": {"title": [{"plain_text": "Питер"}]},
                "Ключ":  {"rich_text": [{"plain_text": "город"}]},
            }},
            {"properties": {
                "Текст": {"title": [{"plain_text": "Москва"}]},
                "Ключ":  {"rich_text": [{"plain_text": "город"}]},
            }},
        ]

    async def fake_user_notion_id(tg_id):
        return ""

    with patch.object(weather, "memory_get", empty_get), \
         patch.object(weather, "query_pages", fake_query), \
         patch.object(weather, "get_user_notion_id", fake_user_notion_id):
        city = await weather._resolve_city_from_memory(42)

    assert captured_kwargs.get("sorts") == [
        {"timestamp": "last_edited_time", "direction": "descending"}
    ]
    assert city == "Saint Petersburg"


def test_cache_ttl_is_short_enough():
    """TTL ≤ 10 минут — чтобы смена локации подхватывалась быстро."""
    from miniapp.backend.routes import weather
    assert weather._CACHE_TTL <= 10 * 60
