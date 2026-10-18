"""tests/test_inventory_ai.py — «что подойдёт от X» через Haiku поверх всего
инвентаря (core/inventory_ai.py + фоллбэк в core/list_manager.inventory_search).

Privacy: generic названия, никаких реальных данных.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core import inventory_ai, list_manager
from core.repos.pg_nexus_lists_repo import BARTER_CATEGORY, InventoryItem, ListItem

ITEMS = [
    {"id": "1", "name": "Сорбент", "category": "Аптечка"},
    {"id": "2", "name": "Пластырь", "category": "Аптечка"},
]


def _run(coro):
    return asyncio.run(coro)


def _llm(ret):
    return patch("core.inventory_ai.ask_claude", AsyncMock(return_value=ret))


def test_picks_items_and_adds_why():
    with _llm('[{"id": "1", "why": "сорбирует токсины"}]'):
        out = _run(inventory_ai.suggest_from_inventory("от похмелья", ITEMS))
    assert out == [{**ITEMS[0], "why": "сорбирует токсины"}]


def test_prompt_contains_whole_inventory():
    mock = AsyncMock(return_value="[]")
    with patch("core.inventory_ai.ask_claude", mock):
        _run(inventory_ai.suggest_from_inventory("q", ITEMS))
    prompt = mock.call_args.kwargs["prompt"]
    assert "Сорбент" in prompt and "Пластырь" in prompt


def test_unknown_and_duplicate_ids_dropped():
    with _llm('[{"id": "99", "why": "x"}, {"id": "1", "why": "a"}, {"id": "1", "why": "b"}]'):
        out = _run(inventory_ai.suggest_from_inventory("q", ITEMS))
    assert [o["id"] for o in out] == ["1"]
    assert out[0]["why"] == "a"


def test_markdown_fence_stripped():
    with _llm('```json\n[{"id": "2", "why": "рана"}]\n```'):
        out = _run(inventory_ai.suggest_from_inventory("q", ITEMS))
    assert [o["id"] for o in out] == ["2"]


def test_bad_json_returns_empty():
    with _llm("не json"):
        assert _run(inventory_ai.suggest_from_inventory("q", ITEMS)) == []


def test_non_list_json_returns_empty():
    with _llm('{"id": "1"}'):
        assert _run(inventory_ai.suggest_from_inventory("q", ITEMS)) == []


def test_llm_error_returns_empty():
    with patch("core.inventory_ai.ask_claude", AsyncMock(side_effect=RuntimeError("boom"))):
        assert _run(inventory_ai.suggest_from_inventory("q", ITEMS)) == []


def test_empty_query_or_inventory_skips_llm():
    mock = AsyncMock()
    with patch("core.inventory_ai.ask_claude", mock):
        assert _run(inventory_ai.suggest_from_inventory("  ", ITEMS)) == []
        assert _run(inventory_ai.suggest_from_inventory("q", [])) == []
    mock.assert_not_called()


def test_pool_capped():
    many = [{"id": str(i), "name": f"n{i}"} for i in range(inventory_ai.MAX_ITEMS + 50)]
    mock = AsyncMock(return_value="[]")
    with patch("core.inventory_ai.ask_claude", mock):
        _run(inventory_ai.suggest_from_inventory("q", many))
    assert f'id={inventory_ai.MAX_ITEMS + 10}' not in mock.call_args.kwargs["prompt"]


# ── inventory_search integration ─────────────────────────────────────────────

def _li(id_, name):
    return ListItem(id=id_, name=name, list_type="инвентарь", status="not_started")


def test_nexus_ilike_hit_skips_ai():
    with patch.object(list_manager._nexus_repo, "search", AsyncMock(return_value=[_li("1", "Сорбент")])), \
         patch("core.inventory_ai.suggest_from_inventory") as ai:
        out = _run(list_manager.inventory_search("сорбент", "☀️ Nexus", "u"))
    ai.assert_not_called()
    assert [r["name"] for r in out] == ["Сорбент"]


def test_nexus_miss_sends_whole_inventory_to_ai():
    calls = []

    async def search(query, **kw):
        calls.append(query)
        return [] if query else [_li("1", "Сорбент"), _li("2", "Пластырь")]

    picked = [{"id": "1", "name": "Сорбент", "why": "сорбирует токсины"}]
    with patch.object(list_manager._nexus_repo, "search", search), \
         patch("core.inventory_ai.suggest_from_inventory", AsyncMock(return_value=picked)) as ai:
        out = _run(list_manager.inventory_search("что-то от похмелья", "☀️ Nexus", "u"))
    assert calls == ["что-то от похмелья", ""]
    q, pool = ai.call_args.args
    assert q == "что-то от похмелья" and [p["name"] for p in pool] == ["Сорбент", "Пластырь"]
    assert out == picked


def test_nexus_ai_failure_returns_empty():
    async def search(query, **kw):
        return [] if query else [_li("1", "Сорбент")]

    with patch.object(list_manager._nexus_repo, "search", search), \
         patch("core.inventory_ai.suggest_from_inventory", AsyncMock(side_effect=RuntimeError("x"))):
        assert _run(list_manager.inventory_search("q", "☀️ Nexus", "u")) == []


def test_arcana_pool_excludes_barter_and_checklists():
    def ii(id_, name, lt="инвентарь", cat=""):
        return InventoryItem(id=id_, name=name, list_type=lt, status="not_started", category=cat)

    async def search(query, **kw):
        return [] if query else [
            ii("1", "Ладан"), ii("2", "Бартер-пункт", cat=BARTER_CATEGORY), ii("3", "Чек", lt="чеклист"),
        ]

    with patch.object(list_manager._arcana_repo, "search", search), \
         patch("core.inventory_ai.suggest_from_inventory", AsyncMock(return_value=[])) as ai:
        _run(list_manager.inventory_search("для очищения", "🌒 Arcana", "u"))
    _, pool = ai.call_args.args
    assert [p["name"] for p in pool] == ["Ладан"]


def test_empty_query_never_calls_ai():
    with patch.object(list_manager._nexus_repo, "search", AsyncMock(return_value=[])), \
         patch("core.inventory_ai.suggest_from_inventory") as ai:
        assert _run(list_manager.inventory_search("", "☀️ Nexus", "u")) == []
    ai.assert_not_called()
