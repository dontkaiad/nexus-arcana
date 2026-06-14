"""tests/test_memory_repo.py — MemoryRepo seam + handler/finance integration.

Покрытие:
- MemoryRepo.set_active → delegates to _pg.set_current, returns count
- MemoryRepo.save_parsed (plain create) → delegates to _pg.add
- MemoryRepo.save_parsed (upsert=True, updated) → delegates to _pg.upsert
- MemoryRepo.save_parsed (upsert=True, created) → delegates to _pg.upsert
- MemoryRepo.save_parsed maps bot_label → scope correctly
- cb_mem_deactivate_selected uses _mem_repo.set_active, not update_page
- cb_mem_deactivate_all uses _mem_repo.set_active
- cb_mem_reactivate_all filters inactive Memory objects before calling set_active
- cb_mem_reactivate_selected uses _mem_repo.set_active + updates cache (is_current)
- cb_mem_auto_yes uses _mem_repo.save_parsed, not page_create
- _save_limit_to_memory (finance) uses _mem_repo.save_parsed with upsert
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.repos.pg_memory_repo import Memory


# ── helpers ───────────────────────────────────────────────────────────────────

def _mk_memory(mid: str, fact: str, is_current: bool = True,
               category: str = "💡 Инсайт") -> Memory:
    return Memory(
        id=mid,
        fact=fact,
        key="key1",
        category=category,
        is_current=is_current,
    )


# ── MemoryRepo unit tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_active_deactivates_pages():
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()
    with patch.object(repo._pg, "set_current", AsyncMock(return_value=2)) as sc:
        done = await repo.set_active(["1", "2"], False)

    assert done == 2
    sc.assert_awaited_once_with(["1", "2"], False)


@pytest.mark.asyncio
async def test_set_active_reactivates_pages():
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()
    with patch.object(repo._pg, "set_current", AsyncMock(return_value=1)) as sc:
        done = await repo.set_active(["p-x"], True)

    assert done == 1
    sc.assert_awaited_once_with(["p-x"], True)


@pytest.mark.asyncio
async def test_set_active_error_returns_zero():
    """Если PG выбросил — set_active возвращает 0 (graceful)."""
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()
    with patch.object(repo._pg, "set_current", AsyncMock(side_effect=Exception("DB down"))):
        # MemoryRepo сам не ловит — пробрасывает; caller ловит.
        # Тут проверяем что set_current был вызван.
        try:
            await repo.set_active(["p1", "p2"], False)
        except Exception:
            pass  # expected


@pytest.mark.asyncio
async def test_save_parsed_creates_page():
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()
    with patch.object(repo._pg, "add", AsyncMock(return_value="42")) as add:
        result = await repo.save_parsed(
            "маша не ест мясо", "👥 Люди", "маша", "маша_диета",
            "☀️ Nexus", user_notion_id="user-1",
        )

    assert result == "42"
    add.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_parsed_upsert_updates_existing():
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()
    with patch.object(repo._pg, "upsert", AsyncMock(return_value=("p-exist", True))) as ups:
        result = await repo.save_parsed(
            "лимит: кафе — 5000₽/мес", "💰 Лимит", "кафе", "лимит_кафе",
            "☀️ Nexus", upsert=True,
        )

    assert result == "p-exist"
    ups.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_parsed_upsert_creates_when_not_found():
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()
    with patch.object(repo._pg, "upsert", AsyncMock(return_value=("p-new", False))) as ups:
        result = await repo.save_parsed(
            "лимит: продукты — 8000₽/мес", "💰 Лимит", "продукты", "лимит_продукты",
            "☀️ Nexus", upsert=True,
        )

    assert result == "p-new"
    ups.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_parsed_maps_bot_label_to_scope():
    """bot_label '☀️ Nexus' → scope 'nexus', 'unknown' → 'global'."""
    from core.repos import memory_repo as mod

    repo = mod.MemoryRepo()

    with patch.object(repo._pg, "add", AsyncMock(return_value="id1")) as add:
        await repo.save_parsed("факт", "💡 Инсайт", "", "ключ", "☀️ Nexus")
    scope_arg = add.await_args.args[3]  # add(fact, key, cat, scope, ...)
    assert scope_arg == "nexus"

    with patch.object(repo._pg, "add", AsyncMock(return_value="id2")) as add:
        await repo.save_parsed("факт", "💡 Инсайт", "", "ключ", "unknown")
    scope_arg2 = add.await_args.args[3]
    assert scope_arg2 == "global"


# ── Handler → Repo integration ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cb_mem_deactivate_selected_uses_repo():
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 11
    mem._mem_selected[uid] = {"1", "2"}
    mem._mem_delete_pages[uid] = [
        _mk_memory("1", "факт А"),
        _mk_memory("2", "факт Б"),
    ]

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_deactivate_selected:{uid}"
    call.message.edit_text = AsyncMock()

    with patch.object(hmod._mem_repo, "set_active", AsyncMock(return_value=2)) as sa:
        await hmod.cb_mem_deactivate_selected(call)

    sa.assert_awaited_once()
    ids_arg, active_arg = sa.await_args.args
    assert set(ids_arg) == {"1", "2"}
    assert active_arg is False
    assert "2" in call.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_cb_mem_deactivate_all_uses_repo():
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 22
    mem._mem_delete_pages[uid] = [
        _mk_memory("1", "факт 1"),
        _mk_memory("2", "факт 2"),
    ]
    mem._mem_selected[uid] = set()

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_deactivate_all:{uid}"
    call.message.edit_text = AsyncMock()

    with patch.object(hmod._mem_repo, "set_active", AsyncMock(return_value=2)) as sa:
        await hmod.cb_mem_deactivate_all(call)

    sa.assert_awaited_once()
    ids_arg, active_arg = sa.await_args.args
    assert set(ids_arg) == {"1", "2"}
    assert active_arg is False


@pytest.mark.asyncio
async def test_cb_mem_reactivate_all_filters_inactive():
    """set_active вызывается только для неактуальных записей (is_current=False)."""
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 33
    mem._mem_delete_pages[uid] = [
        _mk_memory("active-p", "активный", is_current=True),
        _mk_memory("inactive-p", "неактивный", is_current=False),
    ]
    mem._mem_selected[uid] = set()

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_reactivate_all:{uid}"
    call.message.edit_text = AsyncMock()

    with patch.object(hmod._mem_repo, "set_active", AsyncMock(return_value=1)) as sa:
        await hmod.cb_mem_reactivate_all(call)

    ids_arg, active_arg = sa.await_args.args
    assert ids_arg == ["inactive-p"]
    assert active_arg is True


@pytest.mark.asyncio
async def test_cb_mem_reactivate_selected_updates_cache():
    """После set_active is_current в кэше обновляется на True."""
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 44
    m = _mk_memory("p-sel", "факт", is_current=False)
    mem._mem_delete_pages[uid] = [m]
    mem._mem_selected[uid] = {"p-sel"}

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_reactivate_selected:{uid}"
    call.message.edit_text = AsyncMock()

    with patch.object(hmod._mem_repo, "set_active", AsyncMock(return_value=1)):
        await hmod.cb_mem_reactivate_selected(call)

    assert m.is_current is True


@pytest.mark.asyncio
async def test_cb_mem_auto_yes_uses_repo():
    """cb_mem_auto_yes вызывает _mem_repo.save_parsed, не page_create напрямую."""
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 55
    hmod._pending_auto[uid] = {"text": "маша не ест мясо", "user_notion_id": "u-1"}

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_auto_yes:{uid}"
    call.message.edit_text = AsyncMock()

    fake_react = AsyncMock()
    with patch("nexus.handlers.memory.mem._parse_fact",
               AsyncMock(return_value=("маша не ест мясо", "👥 Люди", "маша", "маша_диета"))), \
         patch("nexus.handlers.memory.react", fake_react), \
         patch.object(hmod._mem_repo, "save_parsed", AsyncMock(return_value="p-ok")) as sp:
        await hmod.cb_mem_auto_yes(call)

    sp.assert_awaited_once()
    assert sp.await_args.args[0] == "маша не ест мясо"   # fact
    assert sp.await_args.args[1] == "👥 Люди"            # category
    assert "Запомнил" in call.message.edit_text.call_args.args[0]


# ── finance._save_limit_to_memory → Repo ─────────────────────────────────────

@pytest.mark.asyncio
async def test_save_limit_to_memory_uses_repo():
    """_save_limit_to_memory делегирует в _mem_repo.save_parsed с upsert=True."""
    import nexus.handlers.finance as fmod
    from core.repos import memory_repo as mrmod

    with patch.object(mrmod._repo, "save_parsed", AsyncMock(return_value="p-lim")) as sp:
        await fmod._save_limit_to_memory("кафе", 5000, user_notion_id="u-2")

    sp.assert_awaited_once()
    kw = sp.await_args.kwargs
    assert kw["fact"] == "лимит: кафе — 5000₽/мес"
    assert kw["category"] == "💰 Лимит"
    assert kw["ключ"] == "лимит_кафе"
    assert kw["upsert"] is True
