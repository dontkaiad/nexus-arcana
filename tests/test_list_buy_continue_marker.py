"""tests/test_list_buy_continue_ещё.py — issue #80.

Repro: after a shopping list message ("купи молоко" → 🛒 Добавлено в
покупки), a short follow-up in the shape "<item> ещё" / "ещё <item>" (no
verb) matched no list pre-filter and fell through to the generic
Haiku classifier, which treated it as note/unknown — the item was lost.

Fix: core/list_classifier._LIST_BUY_CONTINUE_RE catches the "ещё" marker
at the start or end of a short message, guarded by
_LIST_BUY_CONTINUE_BLOCK_RE against "ещё раз", questions, sums, and task
verbs — wired into core.classifier.classify() right after the main
_LIST_BUY_RE check.
"""
from __future__ import annotations

import pytest

from core.list_classifier import _LIST_BUY_CONTINUE_RE, _LIST_BUY_CONTINUE_BLOCK_RE


def _is_continue(text: str) -> bool:
    return bool(_LIST_BUY_CONTINUE_RE.search(text) and not _LIST_BUY_CONTINUE_BLOCK_RE.search(text))


@pytest.mark.parametrize("text", [
    "ещё кофе",
    "кофе ещё",
    "ещё кофе и чай",
    "еще кофе",       # без ё — частый вариант набора
    "кофе еще",
])
def test_continue_marker_matches(text):
    assert _is_continue(text), text


@pytest.mark.parametrize("text", [
    "ещё раз",
    "напомни ещё раз",
    "напомни ещё",
    "что ещё",
    "сколько ещё",
    "ещё 300 на кофе",
    "потратила ещё 300 на кофе",
])
def test_continue_marker_guarded_against_false_positives(text):
    assert not _is_continue(text), text


@pytest.mark.asyncio
async def test_classify_routes_continue_marker_to_list_buy():
    from core.classifier import classify
    items = await classify("кофе ещё")
    assert items == [{"type": "list_buy", "text": "кофе ещё"}]


@pytest.mark.asyncio
async def test_classify_does_not_route_ещё_раз_to_list_buy():
    from unittest.mock import AsyncMock, patch
    from core.classifier import classify
    with patch("core.classifier.ask_claude", AsyncMock(return_value='{"type":"unknown"}')):
        items = await classify("напомни ещё раз")
    assert items != [{"type": "list_buy", "text": "напомни ещё раз"}]
