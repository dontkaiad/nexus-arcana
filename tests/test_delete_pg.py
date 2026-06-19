"""tests/test_delete_pg.py — generic /delete migrated to per-domain PG soft-archive.

Покрытие:
- archive_records: правильный soft-метод на домен; rituals → archive (НЕ delete).
- finance/clients target → «недоступно», ничего не выбрано/удалено.
- scope=all → усиленный confirm-текст; _pending хранит (domain, ids, scope).
- confirm с несовпадающим target отклоняется (archive не вызывается).
- _apply_scope: today/last/date/month/all.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── DELETION dispatch: правильный soft-метод на домен ────────────────────────

@pytest.mark.asyncio
async def test_archive_records_tasks_calls_set_archived():
    from core import deleter
    from nexus.repos.pg_tasks_repo import PgTasksRepo
    with patch.object(PgTasksRepo, "set_archived", AsyncMock(return_value=None)) as m:
        n = await deleter.archive_records("tasks", ["1", "2"])
    assert n == 2
    assert m.await_count == 2


@pytest.mark.asyncio
async def test_archive_records_notes_calls_archive():
    from core import deleter
    from nexus.repos.pg_notes_repo import PgNotesRepo
    with patch.object(PgNotesRepo, "archive", AsyncMock(return_value=True)) as m:
        n = await deleter.archive_records("notes", ["7"])
    assert n == 1
    m.assert_awaited_once_with("7")


@pytest.mark.asyncio
async def test_archive_records_sessions_calls_archive():
    from core import deleter
    from arcana.repos.pg_sessions_repo import PgSessionsRepo
    with patch.object(PgSessionsRepo, "archive", AsyncMock(return_value=True)) as m:
        n = await deleter.archive_records("sessions", ["5"])
    assert n == 1
    m.assert_awaited_once_with("5")


@pytest.mark.asyncio
async def test_archive_records_works_sets_archived_status():
    from core import deleter
    from arcana.repos.pg_works_repo import PgWorksRepo
    with patch.object(PgWorksRepo, "set_status", AsyncMock(return_value=True)) as m:
        n = await deleter.archive_records("works", ["9"])
    assert n == 1
    m.assert_awaited_once_with("9", "archived")


@pytest.mark.asyncio
async def test_archive_records_rituals_soft_not_hard_delete():
    from core import deleter
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    with patch.object(PgRitualsRepo, "archive", AsyncMock(return_value=True)) as m_arch, \
         patch.object(PgRitualsRepo, "delete", AsyncMock(return_value=True)) as m_del:
        n = await deleter.archive_records("rituals", ["3"])
    assert n == 1
    m_arch.assert_awaited_once_with("3")
    m_del.assert_not_called()


@pytest.mark.asyncio
async def test_archive_records_gated_domain_noop():
    from core import deleter
    assert await deleter.archive_records("finance", ["1"]) == 0
    assert await deleter.archive_records("clients", ["1"]) == 0


# ── SELECTION scope filter (pure) ────────────────────────────────────────────

def test_apply_scope_variants():
    from core.deleter import _apply_scope
    recs = [
        {"id": "1", "title": "a", "date": "2026-06-19"},
        {"id": "2", "title": "b", "date": "2026-06-01"},
        {"id": "3", "title": "c", "date": "2026-05-10"},
    ]
    # last N → newest first
    last2 = _apply_scope(recs, "last", None, None, 2)
    assert [r["id"] for r in last2] == ["1", "2"]
    # date
    assert [r["id"] for r in _apply_scope(recs, "date", "2026-06-01", None, 1)] == ["2"]
    # month
    assert {r["id"] for r in _apply_scope(recs, "month", None, "2026-06", 1)} == {"1", "2"}
    # all
    assert len(_apply_scope(recs, "all", None, None, 1)) == 3


@pytest.mark.asyncio
async def test_select_records_gated_returns_empty():
    from core import deleter
    assert await deleter.select_records("finance", "all", user_notion_id="u") == []
    assert await deleter.select_records("clients", "all", user_notion_id="u") == []


# ── HANDLER: gating finance/clients ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_nexus_finance_gated(mock_message):
    import nexus.handlers.delete as d
    d._pending.clear()
    msg = mock_message("удали все финансы")
    with patch("core.claude_client.ask_claude", AsyncMock(return_value="finance")), \
         patch.object(d, "select_records", AsyncMock()) as sel, \
         patch.object(d, "archive_records", AsyncMock()) as arch:
        await d.handle_delete(msg, "удали все финансы", user_notion_id="u")
    txt = msg.answer.call_args.args[0]
    assert "недоступно" in txt.lower()
    sel.assert_not_called()
    arch.assert_not_called()
    assert msg.from_user.id not in d._pending


@pytest.mark.asyncio
async def test_arcana_clients_gated(mock_message):
    import arcana.handlers.delete as d
    d._pending.clear()
    msg = mock_message("удали клиента")
    with patch("core.claude_client.ask_claude", AsyncMock(return_value="clients")), \
         patch.object(d, "select_records", AsyncMock()) as sel:
        await d.handle_delete(msg, "удали клиента", user_notion_id="u")
    assert "недоступно" in msg.answer.call_args.args[0].lower()
    sel.assert_not_called()


@pytest.mark.asyncio
async def test_nexus_no_user_fail_closed(mock_message):
    import nexus.handlers.delete as d
    msg = mock_message("удали последнее")
    with patch("core.claude_client.ask_claude", AsyncMock(return_value="tasks")) as ac, \
         patch.object(d, "select_records", AsyncMock()) as sel:
        await d.handle_delete(msg, "удали последнее", user_notion_id="")
    ac.assert_not_called()
    sel.assert_not_called()


# ── HANDLER: scope=all усиленный confirm + (domain,ids,scope) вместе ─────────

@pytest.mark.asyncio
async def test_nexus_scope_all_strong_confirm(mock_message):
    import nexus.handlers.delete as d
    d._pending.clear()
    msg = mock_message("удали все задачи")
    recs = [{"id": "1", "title": "t1", "date": "2026-06-01"},
            {"id": "2", "title": "t2", "date": "2026-06-02"}]
    with patch("core.claude_client.ask_claude", AsyncMock(return_value="tasks")), \
         patch.object(d, "parse_delete_intent", AsyncMock(return_value={"scope": "all", "date": None, "month": None, "count": 1})), \
         patch.object(d, "select_records", AsyncMock(return_value=recs)):
        await d.handle_delete(msg, "удали все задачи", user_notion_id="u")
    prompt = msg.answer.call_args.args[0]
    assert "ВСЕ" in prompt and "⚠️" in prompt
    assert d._pending[msg.from_user.id] == ("tasks", ["1", "2"], "all")


# ── HANDLER: confirm domain-mismatch отклоняется ─────────────────────────────

@pytest.mark.asyncio
async def test_confirm_domain_mismatch_rejected(mock_callback):
    import nexus.handlers.delete as d
    uid = 555
    d._pending[uid] = ("notes", ["1"], "last")   # pending для notes
    cb = mock_callback(data="del_confirm_nexus:tasks", from_id=uid)  # кнопка для tasks
    with patch.object(d, "archive_records", AsyncMock()) as arch:
        await d.confirm_delete(cb)
    arch.assert_not_called()
    assert "устарел" in cb.message.edit_text.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_confirm_happy_path_tasks(mock_callback):
    import nexus.handlers.delete as d
    uid = 556
    d._pending[uid] = ("tasks", ["1", "2"], "last")
    cb = mock_callback(data="del_confirm_nexus:tasks", from_id=uid)
    with patch.object(d, "archive_records", AsyncMock(return_value=2)) as arch, \
         patch("nexus.handlers.tasks._remove_task_jobs", MagicMock()) as rj:
        await d.confirm_delete(cb)
    arch.assert_awaited_once_with("tasks", ["1", "2"])
    assert rj.call_count == 2
    assert "2" in cb.message.edit_text.call_args.args[0]
