"""tests/test_zarya.py — ⭐ Zarya pure helpers + role middleware (#23 / ADR-0026)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from zarya.formatting import (
    epoch, from_epoch, group_slots_by_day, slot_label, wants_slots,
)
from zarya.handlers import RoleMiddleware, _ctx_for

UTC = timezone.utc


@pytest.mark.parametrize("t,hit", [
    ("когда у Кай окно на выходных", True),
    ("@zarya свободные слоты?", True),
    ("хочу записаться на пятницу", True),
    ("забронируй меня", True),
    ("есть время в среду?", True),
    ("привет, как дела", False),
    ("", False),
])
def test_wants_slots(t, hit):
    assert wants_slots(t) is hit


def test_epoch_roundtrip():
    d = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    assert from_epoch(epoch(d)) == d


def test_slot_label_msk():
    # 2026-09-14 is a Monday; 11:00 UTC = 14:00 MSK
    d = datetime(2026, 9, 14, 11, 0, tzinfo=UTC)
    assert slot_label(d, hours=2) == "пн 14.09 14:00–16:00"


def test_group_slots_by_day_caps_days():
    base = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
    slots = [base + timedelta(days=d, hours=h) for d in range(6) for h in (0, 1)]
    grouped = group_slots_by_day(slots, limit_days=3)
    assert len(grouped) == 3
    assert all(len(day) == 2 for _, day in grouped)


@pytest.mark.parametrize("role,ctx", [
    ("admin", "friends"), ("friend", "friends"),
    ("guest", "arcana"), ("", "arcana"),
])
def test_ctx_for(role, ctx):
    assert _ctx_for(role) == ctx


@pytest.mark.asyncio
async def test_role_middleware_attaches_role_and_never_raises():
    mw = RoleMiddleware()
    seen = {}

    async def _handler(event, data):
        seen.update(data)
        return "ok"

    data = {"event_from_user": SimpleNamespace(id=777)}
    with patch("zarya.handlers.booking_role", AsyncMock(return_value="friend")):
        assert await mw(_handler, object(), data) == "ok"
    assert seen["role"] == "friend" and seen["tg_id"] == 777

    # resolver blows up → guest, still calls handler
    data2 = {"event_from_user": SimpleNamespace(id=1)}
    with patch("zarya.handlers.booking_role", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await mw(_handler, object(), data2) == "ok"
    assert seen["role"] == "guest"
