"""tests/test_memory_semantic.py — semantic-фоллбэк в поиске core/memory.py
(#184) + guard'ы, что деструктивные операции его не используют.

Индексация на запись живёт в PgMemoryRepo.add/upsert (#186) —
см. tests/test_memory_repo_indexing.py.

Privacy: generic X/Y/фикстуры, никаких реальных фактов.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core.memory import _semantic_search_memory, save_memory
from core.repos.pg_memory_repo import Memory


def _mk_page(id_: str, fact: str = "факт") -> Memory:
    return Memory(id=id_, fact=fact)


def _mk_message():
    msg = AsyncMock()
    msg.answer = AsyncMock()
    return msg


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


# ── save_memory: ответ пользователю не зависит от индексации ────────────────

def test_save_memory_answers_user_on_success():
    msg = _mk_message()
    with patch("core.memory._parse_fact", AsyncMock(return_value=("факт", "🏠 Быт", "", "ключ"))), \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.add = AsyncMock(return_value="99")
        asyncio.run(save_memory(msg, "текст", "user1", "☀️ Nexus"))
    msg.answer.assert_called()


def test_save_memory_registers_plaque_for_reply_correction():
    """#188: плашка «🧠 Запомнил» должна регистрироваться в message_pages,
    иначе reply-исправление ('это в заметки' и т.п.) падает в общий classify()
    и не понимается ({"type":"unknown"})."""
    msg = _mk_message()
    sent = AsyncMock()
    sent.chat.id = 555
    sent.message_id = 777
    msg.answer = AsyncMock(return_value=sent)
    with patch("core.memory._parse_fact", AsyncMock(return_value=("факт", "🐾 Коты", "", "ключ"))), \
         patch("core.memory._mem_repo") as mem_repo, \
         patch("core.message_pages.save_message_page", AsyncMock()) as save_page:
        mem_repo.add = AsyncMock(return_value="99")
        asyncio.run(save_memory(msg, "текст", "user1", "☀️ Nexus"))
    save_page.assert_called_once_with(
        chat_id=555, message_id=777, page_id="99", page_type="memory", bot="nexus",
    )


def test_save_memory_error_reply_on_write_failure():
    msg = _mk_message()
    with patch("core.memory._parse_fact", AsyncMock(return_value=("факт", "🏠 Быт", "", "ключ"))), \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.add = AsyncMock(return_value=None)  # запись не удалась
        asyncio.run(save_memory(msg, "текст", "user1", "☀️ Nexus"))
    msg.answer.assert_called_once_with("⚠️ Ошибка записи в базу")


# ── деструктивные флоу: semantic отключён (#184) ─────────────────────────────

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
