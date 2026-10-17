"""tests/test_inventory_semantic.py — семантический фоллбэк поиска по инвентарю
Nexus (core/inventory_rag.py + core/list_manager.inventory_search).

Privacy: generic названия, никаких реальных данных.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core import inventory_rag, list_manager
from core.repos.pg_nexus_lists_repo import ListItem


def _item(id_: str, name: str, category: str = "", note: str = "") -> ListItem:
    return ListItem(id=id_, name=name, list_type="инвентарь", status="not_started", category=category, note=note)


# ── embed_text ────────────────────────────────────────────────────────────────

def test_embed_text_joins_non_empty_parts():
    assert inventory_rag.embed_text("Сорбент", "Аптечка", "") == "Сорбент Аптечка"
    assert inventory_rag.embed_text("Сорбент", " ", None) == "Сорбент"


def test_index_item_empty_text_noop():
    with patch("core.inventory_rag._embed") as emb:
        assert inventory_rag.index_item("1", "") is False
    emb.assert_not_called()


def test_index_item_no_embedding_returns_false():
    with patch("core.inventory_rag._embed", return_value=[]):
        assert inventory_rag.index_item("1", "Сорбент") is False


def test_search_empty_query_noop():
    with patch("core.inventory_rag._embed") as emb:
        assert inventory_rag.search_inventory_semantic("  ") == []
    emb.assert_not_called()


def test_search_no_embedding_returns_empty():
    with patch("core.inventory_rag._embed", return_value=[]):
        assert inventory_rag.search_inventory_semantic("от похмелья") == []


# ── rerank ────────────────────────────────────────────────────────────────────

def test_rerank_keeps_only_returned_ids():
    cands = [_item("1", "Сорбент"), _item("2", "Пластырь"), _item("3", "Уголь")]
    with patch("core.inventory_rag.ask_claude", AsyncMock(return_value="[1, 3]")):
        out = asyncio.run(inventory_rag.rerank_inventory_candidates("от похмелья", cands))
    assert [c.id for c in out] == ["1", "3"]


def test_rerank_strips_markdown_fence():
    cands = [_item("1", "Сорбент")]
    with patch("core.inventory_rag.ask_claude", AsyncMock(return_value='```json\n["1"]\n```')):
        out = asyncio.run(inventory_rag.rerank_inventory_candidates("q", cands))
    assert [c.id for c in out] == ["1"]


def test_rerank_bad_json_returns_empty():
    with patch("core.inventory_rag.ask_claude", AsyncMock(return_value="не json")):
        out = asyncio.run(inventory_rag.rerank_inventory_candidates("q", [_item("1", "X")]))
    assert out == []


def test_rerank_llm_error_returns_empty():
    with patch("core.inventory_rag.ask_claude", AsyncMock(side_effect=RuntimeError("boom"))):
        out = asyncio.run(inventory_rag.rerank_inventory_candidates("q", [_item("1", "X")]))
    assert out == []


def test_rerank_no_candidates_skips_llm():
    with patch("core.inventory_rag.ask_claude", AsyncMock()) as llm:
        assert asyncio.run(inventory_rag.rerank_inventory_candidates("q", [])) == []
    llm.assert_not_called()


# ── inventory_search (Nexus) ──────────────────────────────────────────────────

def test_inventory_search_ilike_hit_skips_semantic():
    hit = [_item("1", "Сорбент")]
    with patch.object(list_manager._nexus_repo, "search", AsyncMock(return_value=hit)), \
         patch("core.inventory_rag.search_inventory_semantic") as sem:
        out = asyncio.run(list_manager.inventory_search("сорбент", "☀️ Nexus", "u"))
    sem.assert_not_called()
    assert [r["name"] for r in out] == ["Сорбент"]


def test_inventory_search_falls_back_to_semantic_when_ilike_empty():
    cands = [_item("1", "Сорбент"), _item("2", "Пластырь")]
    with patch.object(list_manager._nexus_repo, "search", AsyncMock(return_value=[])), \
         patch("core.inventory_rag.search_inventory_semantic", return_value=cands) as sem, \
         patch("core.inventory_rag.rerank_inventory_candidates",
               AsyncMock(return_value=[cands[0]])):
        out = asyncio.run(list_manager.inventory_search("что-то от похмелья", "☀️ Nexus", "u"))
    sem.assert_called_once_with("что-то от похмелья", "u")
    assert [r["name"] for r in out] == ["Сорбент"]


def test_inventory_search_semantic_failure_returns_empty():
    with patch.object(list_manager._nexus_repo, "search", AsyncMock(return_value=[])), \
         patch("core.inventory_rag.search_inventory_semantic", side_effect=RuntimeError("db")):
        out = asyncio.run(list_manager.inventory_search("что-то", "☀️ Nexus", "u"))
    assert out == []


def test_inventory_search_arcana_never_uses_semantic():
    with patch.object(list_manager._arcana_repo, "search", AsyncMock(return_value=[])), \
         patch("core.inventory_rag.search_inventory_semantic") as sem:
        out = asyncio.run(list_manager.inventory_search("x", "🌒 Arcana", "u"))
    sem.assert_not_called()
    assert out == []


# ── repo hooks ────────────────────────────────────────────────────────────────

def test_repo_update_text_field_resets_and_reindexes():
    from core.repos import pg_nexus_lists_repo as m
    repo = m.PgNexusListsRepo()

    async def run():
        with patch.object(m, "_nl_update_sync", return_value=True), \
             patch("core.inventory_rag.reset_embedding") as reset, \
             patch.object(m, "_spawn_inventory_index") as spawn:
            await repo.update("5", note="новая заметка")
        return reset, spawn

    reset, spawn = asyncio.run(run())
    reset.assert_called_once_with("5")
    spawn.assert_called_once_with("5")


def test_repo_update_quantity_does_not_reindex():
    from core.repos import pg_nexus_lists_repo as m
    repo = m.PgNexusListsRepo()

    async def run():
        with patch.object(m, "_nl_update_sync", return_value=True), \
             patch("core.inventory_rag.reset_embedding") as reset, \
             patch.object(m, "_spawn_inventory_index") as spawn:
            await repo.update("5", quantity=2.0)
        return reset, spawn

    reset, spawn = asyncio.run(run())
    reset.assert_not_called()
    spawn.assert_not_called()


def test_repo_add_inventory_item_spawns_index_but_not_shopping():
    from core.repos import pg_nexus_lists_repo as m
    repo = m.PgNexusListsRepo()

    async def run(list_type, stored_type):
        fake = ListItem(id="9", name="X", list_type=stored_type, status="not_started")
        with patch.object(m, "_nl_add_sync", return_value=fake), \
             patch.object(m, "_spawn_inventory_index") as spawn:
            await repo.add_item("X", list_type)
        return spawn

    inv = asyncio.run(run("📦 Инвентарь", "инвентарь"))
    inv.assert_called_once_with("9")
    buy = asyncio.run(run("🛒 Покупки", "покупки"))
    buy.assert_not_called()
