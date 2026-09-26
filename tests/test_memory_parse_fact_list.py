"""Баг (prod): «memory _parse_fact error: 'list' object has no attribute 'get'» —
Haiku ответил JSON-массивом фактов вместо одного объекта."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.memory import _parse_fact


@pytest.mark.asyncio
async def test_parse_fact_list_response_merges_facts():
    raw = ('[{"fact":"маша не ест мясо","category":"👥 Люди","связь":"маша","ключ":"маша_диета"},'
           '{"fact":"маша любит кофе","category":"👥 Люди","связь":"маша","ключ":"маша_кофе"}]')
    with patch("core.memory.ask_claude", AsyncMock(return_value=raw)):
        fact, category, link, key = await _parse_fact("запомни маша не ест мясо и любит кофе")
    assert fact == "маша не ест мясо; маша любит кофе"
    assert category == "👥 Люди"
    assert link == "маша"
    assert key == "маша_диета"


@pytest.mark.asyncio
async def test_parse_fact_single_object_unchanged():
    raw = '{"fact":"аллергия на пыль","category":"🏥 Здоровье","связь":"","ключ":"аллергия"}'
    with patch("core.memory.ask_claude", AsyncMock(return_value=raw)):
        assert await _parse_fact("у меня аллергия на пыль") == ("аллергия на пыль", "🏥 Здоровье", "", "аллергия")


@pytest.mark.asyncio
async def test_parse_fact_empty_list_falls_back():
    with patch("core.memory.ask_claude", AsyncMock(return_value="[]")):
        fact, category, _, key = await _parse_fact("запомни что-то")
    assert category == "💡 Инсайт" and key == "факт"
