"""tests/test_inventory_search_phrases.py — свободные фразы поиска по инвентарю
(«что есть дома от похмелья») ловятся быстрым шаблоном, а не уходят в «не понял»."""
from __future__ import annotations

import pytest

from core.list_classifier import _LIST_INV_SEARCH_RE, extract_inventory_query


@pytest.mark.parametrize("text", [
    "что есть дома от похмелья",
    "Что есть дома от головной боли?",
    "что у меня есть от температуры",
    "есть ли у меня дома что-то от похмелья",
    "есть ли дома что-то от аллергии",
    "есть ибупрофен?",
    "дома есть ибупрофен?",
    "есть ли у меня дома парацетамол",
])
def test_search_phrases_match(text):
    assert _LIST_INV_SEARCH_RE.search(text)


@pytest.mark.parametrize("text", [
    "есть ли задача на завтра",
    "есть ли у меня задачи на сегодня",
    "потратила 500 на еду",
    "купи молоко",
    "что мне делать сегодня",
])
def test_non_search_phrases_do_not_match(text):
    assert not _LIST_INV_SEARCH_RE.search(text)


@pytest.mark.parametrize("text,expected", [
    ("что есть дома от похмелья", "от похмелья"),
    ("что у меня есть от температуры?", "от температуры"),
    ("есть ли у меня дома что-то от похмелья", "от похмелья"),
    ("есть ибупрофен?", "ибупрофен"),
    ("дома есть ибупрофен?", "ибупрофен"),
    ("есть ли дома парацетамол", "парацетамол"),
])
def test_extract_inventory_query(text, expected):
    assert extract_inventory_query(text) == expected


def test_extract_falls_back_to_original_text():
    assert extract_inventory_query("любая фраза") == "любая фраза"


@pytest.mark.parametrize("text", [
    "что есть дома от похмелья",
    "есть ли у меня дома что-то от похмелья",
])
def test_classify_routes_to_inventory_search_without_haiku(text):
    import asyncio
    from unittest.mock import AsyncMock, patch
    from core import classifier as clf

    with patch.object(clf, "ask_claude", AsyncMock(return_value="{}")) as haiku:
        out = asyncio.run(clf.classify(text, tz_offset=3))
    assert out and out[0]["type"] == "list_inventory_search"
    haiku.assert_not_awaited()
