"""Regression: the weather widget's hardcoded _CITY_PREFIXES list didn't
include Ufa at all, so "я в Уфе" fell through _normalize_city's fallback
(return the raw, un-normalized Cyrillic text unchanged) straight into the
Open-Meteo geocoder — which has no match for a dative-case Russian string —
causing get_weather to silently return the 0°/"fetch_failed" placeholder.

Fix: added Ufa (and several other major cities missing from the list) to
_CITY_PREFIXES, and added a Haiku-based renormalize-and-retry fallback in
get_weather for whatever still isn't covered by the hardcoded list, so a
niche/unlisted city doesn't just dead-end at 0°.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def test_ufa_normalizes_to_canonical_name():
    from miniapp.backend.routes.weather import _extract_city_from_text
    assert _extract_city_from_text("я в Уфе") == "Ufa"
    assert _extract_city_from_text("сейчас в Уфе") == "Ufa"
    assert _extract_city_from_text("Уфа") == "Ufa"


@pytest.mark.asyncio
async def test_geocoding_miss_retries_via_llm_normalize():
    from miniapp.backend.routes import weather

    calls = []

    async def fake_fetch(city):
        calls.append(city)
        if city == "Ufa":
            return {"city": "Ufa", "temp": 22, "code": 0, "kind": "clear", "description": "Ясно"}
        return None

    with patch.object(weather, "_resolve_city_from_memory", AsyncMock(return_value="Уфе")), \
         patch.object(weather, "_memory_repo", weather._memory_repo), \
         patch.object(weather, "get_user_notion_id", AsyncMock(return_value="u-1")), \
         patch.object(weather, "_cached", lambda tg_id: None), \
         patch.object(weather, "_fetch_openmeteo", fake_fetch), \
         patch.object(weather, "_llm_normalize_city", AsyncMock(return_value="Ufa")), \
         patch.object(weather, "_generate_tip", AsyncMock(return_value="")), \
         patch.object(weather, "_store", lambda tg_id, data: None):
        data = await weather.get_weather(tg_id=1)

    assert calls == ["Уфе", "Ufa"]   # первая попытка raw, вторая — после LLM-нормализации
    assert data["city"] == "Ufa"
    assert data.get("error") is None


@pytest.mark.asyncio
async def test_llm_normalize_returns_none_when_unknown():
    from miniapp.backend.routes.weather import _llm_normalize_city

    with patch("miniapp.backend.routes.weather.ask_claude", AsyncMock(return_value="unknown")):
        assert await _llm_normalize_city("асдфасдф") is None


@pytest.mark.asyncio
async def test_llm_normalize_swallows_errors():
    from miniapp.backend.routes.weather import _llm_normalize_city

    with patch("miniapp.backend.routes.weather.ask_claude", AsyncMock(side_effect=RuntimeError("down"))):
        assert await _llm_normalize_city("уфе") is None
