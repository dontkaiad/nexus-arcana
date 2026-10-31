"""tests/test_memory_semantic.py — информационный поиск core/memory.py идёт через
единый гибрид (core.memory_search) + guard'ы, что деструктивные операции его
не используют (#184).

Индексация на запись живёт в PgMemoryRepo.add/upsert (#186) —
см. tests/test_memory_repo_indexing.py.

Privacy: generic X/Y/фикстуры, никаких реальных фактов.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core.memory import save_memory, parse_and_store
from core.repos.pg_memory_repo import Memory


def _mk_page(id_: str, fact: str = "факт") -> Memory:
    return Memory(id=id_, fact=fact)


def _mk_message():
    msg = AsyncMock()
    msg.answer = AsyncMock()
    return msg


# ── информационный путь = гибрид; деструктивный = ILIKE ───────────────────────

def test_find_pages_by_hint_semantic_uses_hybrid_without_rerank():
    from core.memory import _find_pages_by_hint
    hybrid = AsyncMock(return_value=[_mk_page("1")])
    with patch("core.memory._memory_search.search_memories", hybrid), \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.search = AsyncMock()
        out = asyncio.run(_find_pages_by_hint("запрос", page_size=7, user_id="u1"))
    assert [m.id for m in out] == ["1"]
    kw = hybrid.await_args.kwargs
    assert kw["user_id"] == "u1" and kw["k"] == 7 and kw["do_rerank"] is False
    mem_repo.search.assert_not_called()   # ILIKE в информационном пути больше не участвует


def test_find_pages_by_hint_destructive_keeps_ilike_match_all():
    from core.memory import _find_pages_by_hint
    search = AsyncMock(return_value=[_mk_page("5")])
    with patch("core.memory._memory_search.search_memories", AsyncMock()) as hybrid, \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.search = search
        out = asyncio.run(_find_pages_by_hint("удали X", use_semantic=False, user_id="u1",
                                              confirmed_only=False))
    assert [m.id for m in out] == ["5"]
    hybrid.assert_not_called()
    assert search.await_args.kwargs["match_all"] is True
    assert search.await_args.kwargs["confirmed_only"] is False


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
    find.assert_called_once_with(
        "какой-то хинт", use_semantic=False, user_id="u1", confirmed_only=False,
    )


def test_parse_and_store_diverts_debt_to_debts_table():
    """#6: общее ядро — долг_ ключ уходит в debts, не в memories."""
    debt_repo = AsyncMock()
    with patch("core.memory._parse_fact",
               AsyncMock(return_value=("долг: Маша — 5000₽", "💰 Лимит", "маша", "долг_маша"))), \
         patch("core.repos.pg_debts_repo._repo", debt_repo), \
         patch("core.memory._mem_repo") as mem_repo:
        r = asyncio.run(parse_and_store("долг маше 5000", "owner-1", "☀️ Nexus"))
    assert r["kind"] == "debt" and r["debt_name"] == "маша"
    debt_repo.upsert.assert_awaited_once()
    mem_repo.add.assert_not_called()


def test_parse_and_store_writes_plain_fact():
    with patch("core.memory._parse_fact",
               AsyncMock(return_value=("любит чай", "🛒 Предпочтения", "", "chai"))), \
         patch("core.memory._mem_repo") as mem_repo:
        mem_repo.add = AsyncMock(return_value="m-9")
        r = asyncio.run(parse_and_store("люблю чай", "owner-1", "☀️ Nexus"))
    assert r["kind"] == "memory" and r["memory_id"] == "m-9"
    assert mem_repo.add.await_args.args[6] == "owner-1"  # user_id threaded


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
    find.assert_called_once_with(
        "какой-то хинт", use_semantic=False, user_id="u1", confirmed_only=False,
    )
