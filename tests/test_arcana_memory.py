"""tests/test_arcana_memory.py — память Арканы (intent dispatch + auto-suggest)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── handlers просто проксируют в core.memory с BOT_LABEL=🌒 Arcana ──────────

@pytest.mark.asyncio
async def test_handle_memory_save_uses_arcana_bot_label():
    from arcana.handlers.memory import handle_memory_save, BOT_LABEL
    msg = MagicMock()
    msg.from_user.id = 1
    with patch("core.memory.save_memory", AsyncMock(return_value=None)) as save:
        await handle_memory_save(
            msg, {"text": "запомни Оля любит таро Уэйт"},
            user_notion_id="u1",
        )
    save.assert_awaited_once()
    args = save.await_args.args
    assert args[1] == "запомни Оля любит таро Уэйт"
    assert args[2] == "u1"
    assert args[3] == BOT_LABEL == "🌒 Arcana"


@pytest.mark.asyncio
async def test_handle_memory_search_calls_core_with_del_prefix():
    from arcana.handlers.memory import handle_memory_search
    msg = MagicMock()
    msg.from_user.id = 1
    with patch("core.memory.search_memory", AsyncMock(return_value=None)) as search:
        await handle_memory_search(
            msg, {"query": "что я помню про Олю"}, user_notion_id="u1",
        )
    search.assert_awaited_once()
    kwargs = search.await_args.kwargs
    assert kwargs.get("del_prefix") == "arcmem_del"


@pytest.mark.asyncio
async def test_handle_memory_deactivate_passes_hint():
    from arcana.handlers.memory import handle_memory_deactivate
    msg = MagicMock()
    msg.from_user.id = 1
    with patch("core.memory.deactivate_memory", AsyncMock(return_value=None)) as fn:
        await handle_memory_deactivate(
            msg, {"hint": "колоду Lenormand для Оли"}, user_notion_id="u1",
        )
    fn.assert_awaited_once()
    args = fn.await_args.args
    assert args[1] == "колоду Lenormand для Оли"


@pytest.mark.asyncio
async def test_handle_memory_delete_uses_arcmem_callbacks():
    from arcana.handlers.memory import handle_memory_delete
    msg = MagicMock()
    msg.from_user.id = 1
    with patch("core.memory.delete_memory", AsyncMock(return_value=None)) as fn:
        await handle_memory_delete(
            msg, {"hint": "Lenormand"}, user_notion_id="u1",
        )
    fn.assert_awaited_once()
    kwargs = fn.await_args.kwargs
    assert kwargs.get("del_prefix") == "arcmem_del"
    assert kwargs.get("cancel_cb") == "arcmem_cancel"


# ─── auto-suggest: третье повторение → handle_memory_auto_suggest ───────────

@pytest.mark.asyncio
async def test_auto_suggest_triggers_on_third_repetition():
    from arcana.handlers import memory as ar_mem
    # Изоляция счётчика
    ar_mem._autosuggest_counts.clear()

    msg = MagicMock()
    msg.from_user.id = 999
    msg.answer = AsyncMock()

    with patch("arcana.handlers.memory.handle_memory_auto_suggest",
               AsyncMock(return_value=None)) as suggest:
        # повтор 1, 2 — ничего не предлагает
        await ar_mem.maybe_auto_suggest(msg, "session_done", "расклад Оле на работу", "u1")
        await ar_mem.maybe_auto_suggest(msg, "session_done", "расклад Оле на работу", "u1")
        suggest.assert_not_awaited()
        # повтор 3 — предлагает
        await ar_mem.maybe_auto_suggest(msg, "session_done", "расклад Оле на работу", "u1")
        suggest.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_suggest_skips_unrelated_intents():
    from arcana.handlers import memory as ar_mem
    ar_mem._autosuggest_counts.clear()
    msg = MagicMock()
    msg.from_user.id = 998
    with patch("arcana.handlers.memory.handle_memory_auto_suggest",
               AsyncMock(return_value=None)) as suggest:
        for _ in range(5):
            await ar_mem.maybe_auto_suggest(msg, "finance", "сколько заработала", "u1")
    suggest.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_suggest_separates_topics_per_intent():
    from arcana.handlers import memory as ar_mem
    ar_mem._autosuggest_counts.clear()
    msg = MagicMock()
    msg.from_user.id = 777
    with patch("arcana.handlers.memory.handle_memory_auto_suggest",
               AsyncMock(return_value=None)) as suggest:
        # три разных запроса — счётчик не дойдёт до 3 ни по одному ключу
        await ar_mem.maybe_auto_suggest(msg, "client_info", "клиент Оля", "u1")
        await ar_mem.maybe_auto_suggest(msg, "client_info", "клиент Маша", "u1")
        await ar_mem.maybe_auto_suggest(msg, "client_info", "клиент Аня", "u1")
        suggest.assert_not_awaited()
        # три повторения одной темы — триггер
        for _ in range(3):
            await ar_mem.maybe_auto_suggest(msg, "client_info", "клиент Оля", "u1")
        suggest.assert_awaited_once()


# ─── ROUTER ─── memory-интенты упомянуты в системном промпте ───────────────

def test_router_system_lists_memory_intents():
    from arcana.handlers.base import ROUTER_SYSTEM
    for intent in ("memory_save", "memory_search", "memory_deactivate", "memory_delete"):
        assert intent in ROUTER_SYSTEM, f"{intent} missing in ROUTER_SYSTEM"
    # few-shot с примером «запомни …»
    assert "запомни" in ROUTER_SYSTEM.lower()
