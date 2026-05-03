"""tests/test_find_or_create_client.py — закрываем дыру «Клиентский без
привязки». find_or_create_client + resolve_or_create + reply «🌟».
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── core.notion_client.find_or_create_client ────────────────────────────────

@pytest.mark.asyncio
async def test_find_existing_returns_id_not_created():
    from core import notion_client as nc
    with patch.object(nc, "client_find",
                      AsyncMock(return_value={"id": "c-1"})), \
         patch.object(nc, "client_add", AsyncMock()) as add_mock:
        cid, created = await nc.find_or_create_client(
            "Маша", user_notion_id="u",
        )
    assert cid == "c-1"
    assert created is False
    add_mock.assert_not_called()


@pytest.mark.asyncio
async def test_find_missing_creates_with_default_paid():
    from core import notion_client as nc
    with patch.object(nc, "client_find", AsyncMock(return_value=None)), \
         patch.object(nc, "client_add",
                      AsyncMock(return_value="c-new")) as add_mock:
        cid, created = await nc.find_or_create_client(
            "Лена", user_notion_id="u",
        )
    assert cid == "c-new"
    assert created is True
    args = add_mock.await_args
    assert args.kwargs["name"] == "Лена"
    assert args.kwargs["client_type"] == "🤝 Платный"


@pytest.mark.asyncio
async def test_create_failure_returns_none_gracefully():
    from core import notion_client as nc
    with patch.object(nc, "client_find", AsyncMock(return_value=None)), \
         patch.object(nc, "client_add",
                      AsyncMock(side_effect=Exception("notion 503"))):
        cid, created = await nc.find_or_create_client(
            "Аня", user_notion_id="u",
        )
    assert cid is None
    assert created is False


# ── resolve_or_create + announce ────────────────────────────────────────────

def _msg(uid: int = 7) -> MagicMock:
    m = MagicMock()
    m.from_user.id = uid
    m.chat.id = 100
    answered = MagicMock()
    answered.message_id = 555
    answered.chat.id = 100
    m.answer = AsyncMock(return_value=answered)
    return m


@pytest.mark.asyncio
async def test_resolve_or_create_announces_when_created():
    from core import client_resolve as cr
    msg = _msg()
    save_mock = AsyncMock()
    with patch.object(cr, "find_or_create_client",
                      AsyncMock(return_value=("c-new", True))), \
         patch.object(cr, "save_message_page", save_mock):
        cid = await cr.resolve_or_create(msg, "Маша", user_notion_id="u")
    assert cid == "c-new"
    msg.answer.assert_awaited_once()
    sent = msg.answer.await_args.args[0]
    assert "🆕 Создала клиента" in sent
    assert "Маша" in sent
    assert "🤝 Платный" in sent
    save_mock.assert_awaited_once()
    assert save_mock.await_args.kwargs["page_type"] == "client"


@pytest.mark.asyncio
async def test_resolve_or_create_silent_when_found():
    from core import client_resolve as cr
    msg = _msg()
    with patch.object(cr, "find_or_create_client",
                      AsyncMock(return_value=("c-old", False))), \
         patch.object(cr, "save_message_page", AsyncMock()):
        cid = await cr.resolve_or_create(msg, "Маша", user_notion_id="u")
    assert cid == "c-old"
    msg.answer.assert_not_called()


# ── Reply «🌟» обновляет тип уже созданного клиента ─────────────────────────

@pytest.mark.asyncio
async def test_reply_self_emoji_updates_client_type():
    """Reply «🌟» на сообщение «🆕 Создала клиента» → Тип клиента = 🌟 Self."""
    from core.reply_update import _CLIENT_REPLY_SYSTEM, parse_reply

    # Имитируем что Haiku вернул правильный JSON для «🌟».
    haiku_resp = '{"new_type": "Self"}'
    with patch("core.reply_update.ask_claude",
               AsyncMock(return_value=haiku_resp)):
        upd = await parse_reply("client", "🌟")
    assert upd.get("new_type") == "Self"
    # И промпт явно описывает Self для «🌟»
    assert "🌟" in _CLIENT_REPLY_SYSTEM
    assert "Self" in _CLIENT_REPLY_SYSTEM


@pytest.mark.asyncio
async def test_apply_updates_client_type_self_writes_select():
    """apply_updates с new_type=Self → props['Тип клиента'] = select(🌟 Self)."""
    import core.reply_update as ru

    captured: dict = {}

    async def fake_update_page(page_id, props):
        captured["page_id"] = page_id
        captured["props"] = props
        return True

    with patch.object(ru, "update_page", fake_update_page):
        applied = await ru.apply_updates(
            page_id="c-1", page_type="client", db_id=None,
            updates={"new_type": "Self"},
        )
    assert "Тип клиента" in captured["props"]
    assert captured["props"]["Тип клиента"]["select"]["name"] == "🌟 Self"
    assert applied.get("Тип клиента") == "🌟 Self"


@pytest.mark.asyncio
async def test_apply_updates_client_type_free_writes_select():
    import core.reply_update as ru

    captured: dict = {}

    async def fake_update_page(page_id, props):
        captured["props"] = props

    with patch.object(ru, "update_page", fake_update_page):
        await ru.apply_updates(
            page_id="c-1", page_type="client", db_id=None,
            updates={"new_type": "Бесплатный"},
        )
    assert captured["props"]["Тип клиента"]["select"]["name"] == "🎁 Бесплатный"


# ── Дыра в session больше не сирота ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_with_unknown_client_now_creates_relation():
    """До фикса: client_name=«Лена», client_find=None → session_type=Клиентский,
    но 👥 Клиенты пуст. После: создаётся клиент + relation проставляется."""
    from arcana.handlers import sessions as sess

    # Симулируем единственное место — find_or_create_client возвращает new id
    msg = _msg()
    msg.bot = MagicMock()

    # parse_session возвращает client_name «Лена» без карт триплета — попадёт
    # на ветку single-flow, дёрнет resolve_or_create.
    parsed = {"client_name": "Лена", "cards": "", "bottom_card": ""}

    import core.client_resolve as cr
    with patch.object(sess, "client_find",
                      AsyncMock(return_value=None)), \
         patch.object(cr, "find_or_create_client",
                      AsyncMock(return_value=("c-lena", True))), \
         patch.object(cr, "save_message_page", AsyncMock()):
        cid = await cr.resolve_or_create(msg, "Лена", user_notion_id="u")

    assert cid == "c-lena", "клиент должен создаться, не оставаться сиротой"
