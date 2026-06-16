"""issue #70: смена локации должна обновлять город в Mini App.

Резолвер (PG-native):
1. явный ключ city_{tg_id} побеждает fuzzy-скан (_memory_repo.find_by_exact_key);
2. fuzzy-скан через find_recent — свежая запись (updated_at DESC) побеждает старую.
"""
import pytest
from unittest.mock import AsyncMock, patch

from core.repos.pg_memory_repo import Memory


def _mem(fact, key="", updated_at="", value=""):
    return Memory(id="1", fact=fact, key=key, value=value, updated_at=updated_at)


@pytest.mark.asyncio
async def test_explicit_city_key_overrides_fuzzy_scan():
    """city_{tg_id} найден в find_by_exact_key → find_recent не вызывается."""
    from miniapp.backend.routes import weather

    async def fake_find_by_exact_key(key, user_notion_id="", page_size=1):
        if key == "city_42":
            return [_mem("Питер", key=key)]
        return []

    find_recent = AsyncMock(side_effect=AssertionError("find_recent не должен вызываться при override"))

    with patch.object(weather._memory_repo, "find_by_exact_key", fake_find_by_exact_key), \
         patch.object(weather._memory_repo, "find_recent", find_recent):
        city = await weather._resolve_city_from_memory(42, "")

    assert city == "Saint Petersburg"
    find_recent.assert_not_awaited()


@pytest.mark.asyncio
async def test_fuzzy_scan_sorts_by_updated_at_desc():
    """find_recent вызывается с правильными параметрами; более свежая запись (updated_at DESC) побеждает."""
    from miniapp.backend.routes import weather

    # find_by_exact_key не находит override
    async def no_override(key, user_notion_id="", page_size=1):
        return []

    # find_recent возвращает «Москва» раньше «Питер» в списке,
    # но Питер новее — после сортировки по updated_at DESC он должен победить
    memories = [
        _mem("Москва", key="город", updated_at="2026-06-01T00:00:00"),
        _mem("Питер",  key="город", updated_at="2026-06-10T00:00:00"),
    ]
    captured = {}

    async def fake_find_recent(is_current=None, user_notion_id="", page_size=10):
        captured["is_current"] = is_current
        captured["user_notion_id"] = user_notion_id
        captured["page_size"] = page_size
        return memories

    with patch.object(weather._memory_repo, "find_by_exact_key", no_override), \
         patch.object(weather._memory_repo, "find_recent", fake_find_recent):
        city = await weather._resolve_city_from_memory(42, "user-x")

    # find_recent вызван с нужными параметрами
    assert captured.get("is_current") is True
    assert captured.get("user_notion_id") == "user-x"
    assert captured.get("page_size") == 200
    # Питер (более свежий) победил Москву
    assert city == "Saint Petersburg"


def test_cache_ttl_is_short_enough():
    """TTL ≤ 10 минут — чтобы смена локации подхватывалась быстро."""
    from miniapp.backend.routes import weather
    assert weather._CACHE_TTL <= 10 * 60


def test_turkey_cities_normalize_to_english():
    """issue #70 follow-up: «в Алании» / «в Турции» / падежи турецких городов
    канонизируются в English-имена для Open-Meteo, не «Турции»/«Алании»."""
    from miniapp.backend.routes.weather import _extract_city_from_text
    assert _extract_city_from_text("я в Алании") == "Alanya"
    assert _extract_city_from_text("в Аланье") == "Alanya"
    assert _extract_city_from_text("я в Турции") == "Istanbul"
    assert _extract_city_from_text("сейчас в Анталье") == "Antalya"
    assert _extract_city_from_text("в Стамбуле") == "Istanbul"
    assert _extract_city_from_text("в Батуми") == "Batumi"
