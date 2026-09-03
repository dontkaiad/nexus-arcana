"""tests/test_notes_user_tz.py

Дата заметки (handle_note) — по личному tz пользователя, не серверному.
"""
from __future__ import annotations

from datetime import datetime as _real_dt, timezone as _tzc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.handlers import notes


def _frozen_dt(instant_utc):
    class _DT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
    return _DT


@pytest.mark.asyncio
@pytest.mark.parametrize("tz,expected", [(3, "2026-06-30"), (5, "2026-07-01")])
async def test_handle_note_date_by_user_tz(monkeypatch, tz, expected):
    monkeypatch.setattr(notes, "datetime", _frozen_dt(_real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)))

    captured = {}

    async def fake_save(message, text, tags, date, user_notion_id=""):
        captured["date"] = date

    msg = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock()

    with patch.object(notes, "_save_note", AsyncMock(side_effect=fake_save)), \
         patch.object(notes, "_get_user_tz", AsyncMock(return_value=tz)), \
         patch.object(notes._repo, "get_all_tags", AsyncMock(return_value=[])), \
         patch.object(notes, "ask_claude", AsyncMock(return_value='{"selected":["🧠 Мысль"],"new":[],"needs_confirm":false}')):
        await notes.handle_note(msg, "мысль про бюджет", "db-notes", user_notion_id="u-1")

    assert captured["date"] == expected
