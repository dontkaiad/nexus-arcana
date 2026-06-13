"""tests/test_memory_repo.py — MemoryRepo seam + handler/finance integration.

Покрытие:
- MemoryRepo.set_active → update_page per page_id, returns count
- MemoryRepo.save_parsed (plain create)
- MemoryRepo.save_parsed (upsert=True, existing found → update)
- MemoryRepo.save_parsed (upsert=True, no existing → create)
- cb_mem_deactivate_selected uses _mem_repo.set_active, not update_page
- cb_mem_deactivate_all uses _mem_repo.set_active
- cb_mem_reactivate_all filters inactive pages before calling set_active
- cb_mem_reactivate_selected uses _mem_repo.set_active + updates cache
- cb_mem_auto_yes uses _mem_repo.save_parsed, not page_create
- _save_limit_to_memory (finance) uses _mem_repo.save_parsed with upsert
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _mem_page(page_id: str, fact: str, active: bool = True) -> dict:
    return {
        "id": page_id,
        "properties": {
            "Текст": {"title": [{"plain_text": fact}]},
            "Актуально": {"checkbox": active},
            "Категория": {"select": {"name": "💡 Инсайт"}},
            "Ключ": {"rich_text": [{"plain_text": "key1"}]},
        },
    }


# ── MemoryRepo unit tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_active_deactivates_pages():
    from core.repos import memory_repo as mod

    with patch.object(mod._notion, "update_page", AsyncMock()) as m:
        repo = mod.MemoryRepo()
        done = await repo.set_active(["p1", "p2"], False)

    assert done == 2
    calls = m.await_args_list
    assert calls[0].args == ("p1", {"Актуально": {"checkbox": False}})
    assert calls[1].args == ("p2", {"Актуально": {"checkbox": False}})


@pytest.mark.asyncio
async def test_set_active_reactivates_pages():
    from core.repos import memory_repo as mod

    with patch.object(mod._notion, "update_page", AsyncMock()) as m:
        repo = mod.MemoryRepo()
        done = await repo.set_active(["p-x"], True)

    assert done == 1
    m.assert_awaited_once_with("p-x", {"Актуально": {"checkbox": True}})


@pytest.mark.asyncio
async def test_set_active_partial_failure_returns_partial_count():
    from core.repos import memory_repo as mod

    call_count = 0

    async def flaky_update(pid, props):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("Notion 503")

    with patch.object(mod._notion, "update_page", AsyncMock(side_effect=flaky_update)):
        repo = mod.MemoryRepo()
        done = await repo.set_active(["ok1", "fail", "ok2"], False)

    assert done == 2


@pytest.mark.asyncio
async def test_save_parsed_creates_page():
    from core.repos import memory_repo as mod

    with patch.object(mod._notion, "page_create", AsyncMock(return_value="new-id")), \
         patch.dict(os.environ, {"NOTION_DB_MEMORY": "db-mem"}):
        repo = mod.MemoryRepo()
        result = await repo.save_parsed(
            "маша не ест мясо", "👥 Люди", "маша", "маша_диета",
            "☀️ Nexus", user_notion_id="user-1",
        )

    assert result == "new-id"


@pytest.mark.asyncio
async def test_save_parsed_upsert_updates_existing():
    from core.repos import memory_repo as mod

    existing = [_mem_page("p-exist", "лимит: кафе — 3000₽/мес")]
    with patch.object(mod._notion, "db_query", AsyncMock(return_value=existing)), \
         patch.object(mod._notion, "update_page", AsyncMock()) as upd, \
         patch.object(mod._notion, "page_create", AsyncMock()) as crt, \
         patch.dict(os.environ, {"NOTION_DB_MEMORY": "db-mem"}):
        repo = mod.MemoryRepo()
        result = await repo.save_parsed(
            "лимит: кафе — 5000₽/мес", "💰 Лимит", "кафе", "лимит_кафе",
            "☀️ Nexus", upsert=True,
        )

    assert result == "p-exist"
    upd.assert_awaited_once()
    crt.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_parsed_upsert_creates_when_not_found():
    from core.repos import memory_repo as mod

    with patch.object(mod._notion, "db_query", AsyncMock(return_value=[])), \
         patch.object(mod._notion, "page_create", AsyncMock(return_value="p-new")) as crt, \
         patch.dict(os.environ, {"NOTION_DB_MEMORY": "db-mem"}):
        repo = mod.MemoryRepo()
        result = await repo.save_parsed(
            "лимит: продукты — 8000₽/мес", "💰 Лимит", "продукты", "лимит_продукты",
            "☀️ Nexus", upsert=True,
        )

    assert result == "p-new"
    crt.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_parsed_no_db_id_returns_none():
    from core.repos import memory_repo as mod

    with patch.dict(os.environ, {}, clear=True):
        repo = mod.MemoryRepo()
        result = await repo.save_parsed("факт", "💡 Инсайт", "", "ключ", "☀️ Nexus")

    assert result is None


# ── Handler → Repo integration ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cb_mem_deactivate_selected_uses_repo():
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 11
    mem._mem_selected[uid] = {"pid-a", "pid-b"}
    mem._mem_delete_pages[uid] = [_mem_page("pid-a", "факт А"), _mem_page("pid-b", "факт Б")]

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_deactivate_selected:{uid}"
    call.message.edit_text = AsyncMock()

    with patch.object(hmod._mem_repo, "set_active", AsyncMock(return_value=2)) as sa:
        await hmod.cb_mem_deactivate_selected(call)

    sa.assert_awaited_once()
    ids_arg, active_arg = sa.await_args.args
    assert set(ids_arg) == {"pid-a", "pid-b"}
    assert active_arg is False
    assert "2" in call.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_cb_mem_deactivate_all_uses_repo():
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 22
    mem._mem_delete_pages[uid] = [_mem_page("p1", "факт 1"), _mem_page("p2", "факт 2")]
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
    assert set(ids_arg) == {"p1", "p2"}
    assert active_arg is False


@pytest.mark.asyncio
async def test_cb_mem_reactivate_all_filters_inactive():
    """set_active вызывается только для неактуальных страниц."""
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 33
    mem._mem_delete_pages[uid] = [
        _mem_page("active-p", "активный", active=True),
        _mem_page("inactive-p", "неактивный", active=False),
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
    """После set_active флаг Актуально в кэше обновляется."""
    import nexus.handlers.memory as hmod
    import core.memory as mem

    uid = 44
    pages = [_mem_page("p-sel", "факт", active=False)]
    mem._mem_delete_pages[uid] = pages
    mem._mem_selected[uid] = {"p-sel"}

    call = MagicMock()
    call.answer = AsyncMock()
    call.from_user.id = uid
    call.data = f"mem_reactivate_selected:{uid}"
    call.message.edit_text = AsyncMock()

    with patch.object(hmod._mem_repo, "set_active", AsyncMock(return_value=1)):
        await hmod.cb_mem_reactivate_selected(call)

    # кэш обновлён
    assert pages[0]["properties"]["Актуально"]["checkbox"] is True


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
    with patch.dict(os.environ, {"NOTION_DB_MEMORY": "db-m"}), \
         patch("nexus.handlers.memory.mem._parse_fact",
               AsyncMock(return_value=("маша не ест мясо", "👥 Люди", "маша", "маша_диета"))), \
         patch("nexus.handlers.memory.react", fake_react), \
         patch.object(hmod._mem_repo, "save_parsed", AsyncMock(return_value="p-ok")) as sp:
        await hmod.cb_mem_auto_yes(call)

    sp.assert_awaited_once()
    kwargs = sp.await_args
    assert kwargs.args[0] == "маша не ест мясо"  # fact
    assert kwargs.args[1] == "👥 Люди"            # category
    assert "Запомнил" in call.message.edit_text.call_args.args[0]


# ── finance._save_limit_to_memory → Repo ─────────────────────────────────────

@pytest.mark.asyncio
async def test_save_limit_to_memory_uses_repo():
    """_save_limit_to_memory делегирует в _mem_repo.save_parsed с upsert=True."""
    import nexus.handlers.finance as fmod
    from core.repos import memory_repo as mrmod

    with patch.dict(os.environ, {"NOTION_DB_MEMORY": "db-m"}), \
         patch.object(mrmod._repo, "save_parsed", AsyncMock(return_value="p-lim")) as sp:
        await fmod._save_limit_to_memory("кафе", 5000, user_notion_id="u-2")

    sp.assert_awaited_once()
    kw = sp.await_args.kwargs
    assert kw["fact"] == "лимит: кафе — 5000₽/мес"
    assert kw["category"] == "💰 Лимит"
    assert kw["ключ"] == "лимит_кафе"
    assert kw["upsert"] is True
