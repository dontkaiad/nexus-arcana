"""tests/test_pending_state_sqlite_migration.py — issues #105 and #114.

Both grimoire's "🔍 Поиск" flow and notes' tag-replace flow kept pending
state in a bare in-memory dict, unlike every other multi-step flow in this
codebase (list_manager, tasks, clients, tarot — see core/task_reminder_msg.py,
arcana/pending_clients.py, arcana/pending_tarot.py). A bot restart between
the button click and the follow-up message silently dropped the pending
state. Both are now SQLite-backed, same TTL-pop pattern as pending_tarot.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestPendingGrimoireSearch:
    """arcana/pending_grimoire_search.py (#105)."""

    @pytest.mark.asyncio
    async def test_save_and_pop_roundtrip(self):
        from arcana.pending_grimoire_search import save_pending_search, pop_pending_search
        await save_pending_search(99993, "user-notion-abc")
        result = await pop_pending_search(99993)
        assert result == "user-notion-abc"

    @pytest.mark.asyncio
    async def test_pop_consumes_state(self):
        """Popped once → second pop finds nothing (matches old dict.pop semantics)."""
        from arcana.pending_grimoire_search import save_pending_search, pop_pending_search
        await save_pending_search(99992, "u")
        await pop_pending_search(99992)
        second = await pop_pending_search(99992)
        assert second is None

    @pytest.mark.asyncio
    async def test_pop_missing_returns_none(self):
        from arcana.pending_grimoire_search import pop_pending_search
        assert await pop_pending_search(999991) is None

    @pytest.mark.asyncio
    async def test_empty_user_notion_id_is_not_treated_as_missing(self):
        """Empty string is a valid stored value (no linked Notion user) — must
        not be conflated with 'no pending state' (that's why pop returns
        Optional[str] with None sentinel, not a truthiness check)."""
        from arcana.pending_grimoire_search import save_pending_search, pop_pending_search
        await save_pending_search(99990, "")
        result = await pop_pending_search(99990)
        assert result == ""

    @pytest.mark.asyncio
    async def test_check_pending_search_routes_to_handler(self):
        from arcana.handlers import grimoire
        from arcana.pending_grimoire_search import save_pending_search

        await save_pending_search(99989, "u-1")
        msg = AsyncMock()
        msg.from_user.id = 99989

        with patch.object(grimoire, "handle_grimoire_search", AsyncMock()) as m:
            handled = await grimoire.check_pending_search(msg, "поиск запрос")

        assert handled is True
        m.assert_awaited_once_with(msg, "поиск запрос", "u-1")

    @pytest.mark.asyncio
    async def test_check_pending_search_false_when_nothing_pending(self):
        from arcana.handlers import grimoire

        msg = AsyncMock()
        msg.from_user.id = 999988
        handled = await grimoire.check_pending_search(msg, "some text")
        assert handled is False


class TestPendingNoteEdit:
    """nexus/pending_note_edit.py (#114)."""

    @pytest.mark.asyncio
    async def test_save_and_pop_roundtrip(self):
        from nexus.pending_note_edit import save_pending_note_edit, pop_pending_note_edit
        await save_pending_note_edit(99988, "page-1", ["a", "b"], "c")
        result = await pop_pending_note_edit(99988)
        assert result == {"page_id": "page-1", "current_tags": ["a", "b"], "new_value": "c"}

    @pytest.mark.asyncio
    async def test_pop_consumes_state(self):
        from nexus.pending_note_edit import save_pending_note_edit, pop_pending_note_edit
        await save_pending_note_edit(99987, "page-2", ["x"], "y")
        await pop_pending_note_edit(99987)
        assert await pop_pending_note_edit(99987) is None

    @pytest.mark.asyncio
    async def test_pop_missing_returns_none(self):
        from nexus.pending_note_edit import pop_pending_note_edit
        assert await pop_pending_note_edit(999986) is None

    @pytest.mark.asyncio
    async def test_note_replace_callback_uses_persisted_state(self):
        from nexus.handlers import notes
        from nexus.pending_note_edit import save_pending_note_edit

        uid = 99985
        await save_pending_note_edit(uid, "page-3", ["старый", "другой"], "новый")

        query = AsyncMock()
        query.data = f"note_replace:{uid}:старый:новый"
        query.from_user.id = uid
        query.message.edit_text = AsyncMock()

        with patch.object(notes._repo, "update_tags", AsyncMock()) as m_update:
            await notes.handle_note_callback(query)

        m_update.assert_awaited_once_with("page-3", ["новый", "другой"])
        query.message.edit_text.assert_awaited_once()
        assert "истекла" not in query.message.edit_text.await_args.args[0]

    @pytest.mark.asyncio
    async def test_note_replace_expired_reports_session_expired(self):
        from nexus.handlers import notes

        query = AsyncMock()
        uid = 999984
        query.data = f"note_replace:{uid}:старый:новый"
        query.from_user.id = uid
        query.message.edit_text = AsyncMock()

        await notes.handle_note_callback(query)

        query.message.edit_text.assert_awaited_once()
        assert "истекла" in query.message.edit_text.await_args.args[0]
