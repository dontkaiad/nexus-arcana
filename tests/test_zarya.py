"""tests/test_zarya.py — ⭐ Zarya pure helpers + role middleware (#23 / ADR-0026)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from zarya.formatting import (
    epoch, from_epoch, group_slots_by_day, slot_label, wants_slots,
)
from zarya.handlers import (
    RoleMiddleware, _ctx_for, cmd_start, on_login_confirm, on_login_deny,
)

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


# ── /start login_<token> — bot-approval login (#23 follow-up) ───────────────


def _msg():
    m = SimpleNamespace()
    m.answer = AsyncMock()
    return m


def _cmd(args):
    return SimpleNamespace(args=args)


@pytest.mark.asyncio
async def test_start_plain_ignores_login_flow():
    m = _msg()
    await cmd_start(m, command=_cmd(None), role="guest")
    m.answer.assert_awaited_once()
    assert "Zarya" in m.answer.call_args[0][0]  # normal /start greeting, not the login flow


@pytest.mark.asyncio
async def test_start_login_unknown_token():
    m = _msg()
    with patch("zarya.handlers.login_tokens_mod.get_pending", AsyncMock(return_value=None)):
        await cmd_start(m, command=_cmd("login_bogus"), role="guest")
    assert "не найдена" in m.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_start_login_expired_token():
    m = _msg()
    pending = {"status": "expired", "tg_id": None}
    with patch("zarya.handlers.login_tokens_mod.get_pending", AsyncMock(return_value=pending)):
        await cmd_start(m, command=_cmd("login_tok1"), role="guest")
    assert "устарела" in m.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_start_login_already_used_token():
    m = _msg()
    pending = {"status": "approved", "tg_id": 111}
    with patch("zarya.handlers.login_tokens_mod.get_pending", AsyncMock(return_value=pending)):
        await cmd_start(m, command=_cmd("login_tok1"), role="guest")
    assert "использована" in m.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_start_login_pending_shows_confirm_buttons():
    m = _msg()
    pending = {"status": "pending", "tg_id": None}
    with patch("zarya.handlers.login_tokens_mod.get_pending", AsyncMock(return_value=pending)):
        await cmd_start(m, command=_cmd("login_tok1"), role="guest")
    text, kwargs = m.answer.call_args[0][0], m.answer.call_args[1]
    assert "Я Заря" in text
    kb = kwargs["reply_markup"].inline_keyboard
    assert kb[0][0].callback_data == "z:login_ok:tok1"
    assert kb[0][1].callback_data == "z:login_no:tok1"


def _call(data, tg_id=67686090):
    c = SimpleNamespace()
    c.data = data
    c.message = SimpleNamespace(edit_text=AsyncMock())
    c.answer = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_on_login_confirm_success():
    c = _call("z:login_ok:tok1")
    with patch("zarya.handlers.login_tokens_mod.approve", AsyncMock(return_value=True)):
        await on_login_confirm(c, tg_id=67686090)
    assert "подтверждён" in c.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_on_login_confirm_stale():
    c = _call("z:login_ok:tok1")
    with patch("zarya.handlers.login_tokens_mod.approve", AsyncMock(return_value=False)):
        await on_login_confirm(c, tg_id=67686090)
    assert "неактуальна" in c.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_on_login_deny():
    c = _call("z:login_no:tok1")
    with patch("zarya.handlers.login_tokens_mod.deny", AsyncMock(return_value=True)) as deny_mock:
        await on_login_deny(c, tg_id=67686090)
    deny_mock.assert_awaited_once_with("tok1", 67686090)
    assert "отклонён" in c.message.edit_text.call_args[0][0]
