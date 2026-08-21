"""tests/test_memory_semantic.py — RAG-хуки в core/memory.py: индексация на
запись и semantic-фоллбэк в поиске (#184).

Privacy: generic X/Y/фикстуры, никаких реальных фактов.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core.memory import _rag_index_memory_safe, _semantic_search_memory, save_memory
from core.repos.pg_memory_repo import Memory


def _mk_page(id_: str, fact: str = "факт") -> Memory:
    return Memory(id=id_, fact=fact)


# ── _rag_index_memory_safe ───────────────────────────────────────────────────

def test_rag_index_memory_safe_calls_index_memory():
    with patch("core.memory_rag.index_memory") as idx:
        idx.return_value = True
        asyncio.run(_rag_index_memory_safe("1", "факт", "🏠 Быт", "связь"))
    idx.assert_called_once_with("1", "факт связь 🏠 Быт")


def test_rag_index_memory_safe_never_raises():
    with patch("core.memory_rag.index_memory", side_effect=RuntimeError("boom")):
        # не должно бросать наружу
        asyncio.run(_rag_index_memory_safe("1", "факт", "🏠 Быт", ""))


# ── _semantic_search_memory ──────────────────────────────────────────────────

def test_semantic_search_skipped_when_ilike_found_enough():
    existing = [_mk_page("1"), _mk_page("2"), _mk_page("3")]
    with patch("core.memory_rag.search_memory_semantic") as sem:
        out = asyncio.run(_semantic_search_memory("запрос", existing))
    sem.assert_not_called()
    assert out == existing


def test_semantic_search_called_when_ilike_thin():
    existing = [_mk_page("1")]
    extra = [_mk_page("2"), _mk_page("1")]  # "1" дубль — должен схлопнуться
    with patch("core.memory_rag.search_memory_semantic", return_value=extra):
        out = asyncio.run(_semantic_search_memory("запрос", existing))
    ids = [m.id for m in out]
    assert ids == ["1", "2"]  # ILIKE-хиты первыми, дубли не повторяются


def test_semantic_search_empty_query_noop():
    existing = []
    with patch("core.memory_rag.search_memory_semantic") as sem:
        out = asyncio.run(_semantic_search_memory("", existing))
    sem.assert_not_called()
    assert out == []


def test_semantic_search_failure_returns_existing():
    existing = [_mk_page("1")]
    with patch("core.memory_rag.search_memory_semantic", side_effect=RuntimeError("boom")):
        out = asyncio.run(_semantic_search_memory("запрос", existing))
    assert out == existing  # сбой semantic не всплывает


# ── save_memory → RAG-индексация вызывается после успешной записи ──────────

def _mk_message():
    msg = AsyncMock()
    msg.answer = AsyncMock()
    return msg


def test_save_memory_indexes_after_successful_write():
    msg = _mk_message()
    with patch("core.memory._parse_fact", AsyncMock(return_value=("факт", "🏠 Быт", "", "ключ"))), \
         patch("core.memory._mem_repo") as mem_repo, \
         patch("core.memory._rag_index_memory_safe", AsyncMock()) as idx:
        mem_repo.add = AsyncMock(return_value="99")
        asyncio.run(save_memory(msg, "текст", "user1", "☀️ Nexus"))
    idx.assert_called_once_with("99", "факт", "🏠 Быт", "")
    msg.answer.assert_called()  # пользовательский ответ всё равно отправлен


def test_deactivate_memory_hint_path_disables_semantic():
    """«забудь X» деактивирует ВСЁ найденное без подтверждения — semantic
    (top-K без порога похожести) там запрещён (#184)."""
    msg = _mk_message()
    msg.from_user.id = 7
    find = AsyncMock(return_value=[_mk_page("1")])
    with patch("core.memory._find_pages_by_hint", find), \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.set_active = AsyncMock(return_value=1)
        from core.memory import deactivate_memory
        asyncio.run(deactivate_memory(msg, "какой-то хинт", "u1"))
    find.assert_called_once_with("какой-то хинт", use_semantic=False)


def test_delete_memory_hint_path_disables_semantic():
    """«удали из памяти X» при 1 матче архивирует НЕМЕДЛЕННО — semantic
    там запрещён (#184)."""
    msg = _mk_message()
    msg.from_user.id = 8
    find = AsyncMock(return_value=[_mk_page("1")])
    with patch("core.memory._find_pages_by_hint", find), \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.archive = AsyncMock(return_value=True)
        from core.memory import delete_memory
        asyncio.run(delete_memory(msg, "какой-то хинт", "u1"))
    find.assert_called_once_with("какой-то хинт", use_semantic=False)


def test_save_memory_no_index_on_write_failure():
    msg = _mk_message()
    with patch("core.memory._parse_fact", AsyncMock(return_value=("факт", "🏠 Быт", "", "ключ"))), \
         patch("core.memory._mem_repo") as mem_repo, \
         patch("core.memory._rag_index_memory_safe", AsyncMock()) as idx:
        mem_repo.add = AsyncMock(return_value=None)  # запись не удалась
        asyncio.run(save_memory(msg, "текст", "user1", "☀️ Nexus"))
    idx.assert_not_called()
