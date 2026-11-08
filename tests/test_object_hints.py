"""tests/test_object_hints.py — «как обычно» при создании задачи и предпочтения списков идут через гибридный поиск, а не ILIKE.
Синтетический мир (evals), вектор выключен; ловушки: отрицание, аллергия, факт другого человека."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import insert

from core import list_manager, memory as mem
from core import memory_search as ms
from core.db import get_engine
from core.repos.memories_table import memories
from evals.synthetic.memory_world import SYN_USER, world


@pytest.fixture
def w():
    with world() as ids, patch.object(ms, "_embed", return_value=[]):
        yield ids


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_recall_returns_positive_fact_only(w):
    assert "овсяное молоко" in _run(mem.recall_from_memory("молоко", user_id=SYN_USER))
    assert _run(mem.recall_from_memory("рыбу", user_id=SYN_USER)) is None         # «не ем рыбу» ≠ «как обычно рыбу»
    assert _run(mem.recall_from_memory("орехи", user_id=SYN_USER)) is None        # аллергия — не подсказка; факт Веры не берётся
    assert _run(mem.recall_from_memory("гранат", user_id=SYN_USER)) is None
    assert "чёрный чай" in _run(mem.recall_from_memory("чай", user_id=SYN_USER))   # зелёный чай Веры — чужой
    assert _run(mem.recall_from_memory("  ")) is None


def test_recall_does_not_use_ilike_repo(w):
    with patch.object(mem._mem_repo, "search", side_effect=AssertionError("ILIKE-путь не должен вызываться")):
        assert _run(mem.recall_from_memory("молоко", user_id=SYN_USER))


def test_list_prefs_use_hybrid_and_skip_negative(w):
    pref = _run(list_manager._search_memory_for_prefs("молоко", SYN_USER))
    assert "овсяное молоко марки" in pref and "не пью" not in pref
    assert _run(list_manager._search_memory_for_prefs("орехи", SYN_USER)) == ""


def test_list_category_mapping_from_memory(w):
    cat = list_manager.LIST_CATEGORIES[0]
    word = (cat.split(" ", 1)[-1] if " " in cat else cat)
    with get_engine().begin() as c:
        c.execute(insert(memories).values(fact_text=f"сыр относится к категории {word}", key_name="", category="🛒 Предпочтения",
                                          user_id=SYN_USER, review_status="confirmed"))
    got = _run(list_manager.search_memory_categories(["сыр", "гранат"], SYN_USER))
    assert got == {"сыр": cat}


def test_object_facts_needs_lexical_match_even_if_vector_finds_something(w):
    fake_vec = [[0.1] * 1024]
    with patch.object(ms, "_embed", return_value=fake_vec):       # вектор «находит» ближайшее, но слова в факте нет
        assert _run(ms.object_facts("гранат", user_id=SYN_USER)) == []
