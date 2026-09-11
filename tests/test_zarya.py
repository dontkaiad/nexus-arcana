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
    RoleMiddleware, _bot_addressed, _ctx_for, cmd_help, cmd_start,
    on_login_confirm, on_login_deny,
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
    m.chat = SimpleNamespace(type="private")  # DM by default — _bot_addressed() always True there
    m.entities = None
    m.reply_to_message = None
    return m


def _cmd(args):
    return SimpleNamespace(args=args)


@pytest.mark.asyncio
async def test_start_plain_ignores_login_flow():
    m = _msg()
    await cmd_start(m, command=_cmd(None), role="guest")
    m.answer.assert_awaited_once()
    assert "Заря" in m.answer.call_args[0][0]  # normal /start greeting, not the login flow


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


# ── /help ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_help_admin_lists_commands():
    m = _msg()
    await cmd_help(m, role="admin")
    text = m.answer.call_args[0][0]
    assert "/requests" in text and "/bookings" in text and "/slots" in text


@pytest.mark.asyncio
async def test_help_friend_no_admin_commands():
    m = _msg()
    await cmd_help(m, role="friend")
    text = m.answer.call_args[0][0]
    assert "/slots" in text
    assert "/requests" not in text


# ── fallback: unrecognized text никогда не пропадает молча (#226) ───────────

@pytest.mark.asyncio
async def test_unrecognized_text_falls_back_when_classify_fails():
    from zarya.handlers import on_unrecognized
    m = _msg()
    m.text = "какая-то дичь"
    with patch("zarya.classifier.classify_zarya", AsyncMock(return_value={"intent": "chat", "reply": ""})):
        await on_unrecognized(m, role="guest")
    m.answer.assert_awaited_once()
    assert "/help" in m.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_unrecognized_routes_to_slots():
    from zarya.handlers import on_unrecognized
    m = _msg()
    m.text = "и где"
    with patch("zarya.classifier.classify_zarya", AsyncMock(return_value={"intent": "slots", "reply": ""})) as clf, \
         patch("zarya.handlers._show_slots", AsyncMock()) as show_slots:
        await on_unrecognized(m, role="friend")
    show_slots.assert_awaited_once_with(m, "friend")
    clf.assert_awaited_once_with("и где", role="friend")  # Заря знает КТО спрашивает


@pytest.mark.asyncio
async def test_unrecognized_routes_to_help():
    from zarya.handlers import on_unrecognized
    m = _msg()
    m.text = "что ты умеешь вообще"
    with patch("zarya.classifier.classify_zarya", AsyncMock(return_value={"intent": "help", "reply": ""})), \
         patch("zarya.handlers.cmd_help", AsyncMock()) as help_mock:
        await on_unrecognized(m, role="guest")
    help_mock.assert_awaited_once_with(m, role="guest")


@pytest.mark.asyncio
async def test_unrecognized_chat_reply_in_character():
    from zarya.handlers import on_unrecognized
    m = _msg()
    m.text = "тупая машина"
    with patch("zarya.classifier.classify_zarya",
               AsyncMock(return_value={"intent": "chat", "reply": "Ауч 🙈 Не машина!"})):
        await on_unrecognized(m, role="guest")
    m.answer.assert_awaited_once_with("Ауч 🙈 Не машина!")


@pytest.mark.asyncio
async def test_unrecognized_swallows_classify_exception():
    from zarya.handlers import on_unrecognized
    m = _msg()
    m.text = "что-нибудь"
    with patch("zarya.classifier.classify_zarya", AsyncMock(side_effect=RuntimeError("api down"))):
        await on_unrecognized(m, role="guest")
    m.answer.assert_awaited_once()
    assert "/help" in m.answer.call_args[0][0]


# ── _bot_addressed: group privacy теперь off → всё видит, но молчит если не
# обратились явно (#226 follow-up — иначе лезла бы с «не поняла» на любую
# реплику между людьми в группе) ────────────────────────────────────────────

def _group_msg(text="", mention=False, reply_to_bot=False):
    m = SimpleNamespace()
    m.answer = AsyncMock()
    m.chat = SimpleNamespace(type="group")
    m.text = text
    m.bot = SimpleNamespace(id=999)
    m.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=999)) if reply_to_bot else None
    if mention:
        needle = "@heylark_booking_bot"
        offset = text.find(needle)
        m.entities = [SimpleNamespace(type="mention", offset=offset, length=len(needle))]
    else:
        m.entities = []
    return m


def test_dm_always_addressed():
    assert _bot_addressed(_msg()) is True


def test_group_plain_chatter_not_addressed():
    m = _group_msg("не, я на созвоне в 3")
    assert _bot_addressed(m) is False


def test_group_mention_is_addressed():
    m = _group_msg("@heylark_booking_bot когда у меня свободные окна", mention=True)
    assert _bot_addressed(m) is True


def test_group_reply_to_bot_is_addressed():
    m = _group_msg("и где", reply_to_bot=True)
    assert _bot_addressed(m) is True


@pytest.mark.asyncio
async def test_group_unaddressed_message_gets_no_reply():
    from zarya.handlers import on_unrecognized
    m = _group_msg("го обедать")
    await on_unrecognized(m, role="guest")
    m.answer.assert_not_awaited()


# ── classify_zarya: знает КТО пишет (admin = сама Кай) ───────────────────────

@pytest.mark.asyncio
async def test_classify_zarya_tags_role_in_prompt():
    from zarya.classifier import classify_zarya
    with patch("zarya.classifier.ask_claude",
               AsyncMock(return_value='{"intent":"chat","reply":"ок"}')) as ask:
        await classify_zarya("тупая машина", role="admin")
    prompt = ask.call_args[0][0]
    assert prompt.startswith("Роль пишущего: admin\n")
    assert "тупая машина" in prompt


@pytest.mark.asyncio
async def test_classify_zarya_defaults_to_guest_role():
    from zarya.classifier import classify_zarya
    with patch("zarya.classifier.ask_claude",
               AsyncMock(return_value='{"intent":"chat","reply":"ок"}')) as ask:
        await classify_zarya("привет")
    assert ask.call_args[0][0].startswith("Роль пишущего: guest\n")
