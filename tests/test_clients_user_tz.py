"""tests/test_clients_user_tz.py

Штамп «Дата» при создании клиента (Arcana) — по личному tz пользователя.
"""
from __future__ import annotations

from datetime import datetime as _real_dt, timezone as _tzc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers import clients


def _frozen_dt(instant_utc):
    class _DT(_real_dt):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)
    return _DT


@pytest.mark.asyncio
@pytest.mark.parametrize("tz,expected", [(3, "2026-06-30"), (5, "2026-07-01")])
async def test_today_for_uses_user_tz(monkeypatch, tz, expected):
    monkeypatch.setattr(clients, "datetime", _frozen_dt(_real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)))
    with patch("core.shared_handlers.get_user_tz", AsyncMock(return_value=tz)):
        assert await clients._today_for(42) == expected


@pytest.mark.asyncio
async def test_today_for_no_uid_defaults_to_msk(monkeypatch):
    monkeypatch.setattr(clients, "datetime", _frozen_dt(_real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)))
    with patch("core.shared_handlers.get_user_tz", AsyncMock(return_value=99)) as m:
        assert await clients._today_for(0) == "2026-06-30"
    m.assert_not_called()


@pytest.mark.asyncio
async def test_handle_add_client_stamps_user_tz_date(monkeypatch):
    monkeypatch.setattr(clients, "datetime", _frozen_dt(_real_dt(2026, 6, 30, 20, 0, tzinfo=_tzc.utc)))

    add = AsyncMock(return_value="page-1")
    msg = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock()

    with patch.object(clients, "ask_claude", AsyncMock(return_value='{"name": "Оля"}')), \
         patch.object(clients._repo, "find", AsyncMock(return_value=None)), \
         patch.object(clients._repo, "add", add), \
         patch("core.shared_handlers.get_user_tz", AsyncMock(return_value=5)), \
         patch("arcana.pending_clients.save_pending_client", AsyncMock()), \
         patch("core.client_resolve.announce_client_created", AsyncMock(), create=True):
        await clients.handle_add_client(msg, "создай клиента Оля", user_notion_id="u-1")

    assert add.call_args.kwargs["date"] == "2026-07-01"
