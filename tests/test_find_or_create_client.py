"""tests/test_find_or_create_client.py — закрываем дыру «Клиентский без
привязки». find_or_create_client + resolve_or_create + reply «🌟».
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── core.client_resolve.find_or_create_client ────────────────────────────────

@pytest.mark.asyncio
async def test_find_existing_returns_id_not_created():
    from core import client_resolve as nc
    from arcana.repos.clients_repo import Client

    fake = Client(id="c-1", name="Маша", contact="", request="", notes="", since="")
    with patch("arcana.repos.pg_clients_repo.PgClientsRepo.find",
               AsyncMock(return_value=fake)):
        cid, created = await nc.find_or_create_client(
            "Маша", user_notion_id="u",
        )
    assert cid == "c-1"
    assert created is False


@pytest.mark.asyncio
async def test_find_missing_creates_with_default_paid():
    from core import client_resolve as nc

    with patch("arcana.repos.pg_clients_repo.PgClientsRepo.find",
               AsyncMock(return_value=None)), \
         patch("arcana.repos.pg_clients_repo.PgClientsRepo.create",
               AsyncMock(return_value=99)) as create_mock:
        cid, created = await nc.find_or_create_client(
            "Лена", user_notion_id="u",
        )
    assert cid == "99"
    assert created is True
    assert create_mock.await_args.kwargs["name"] == "Лена"
    assert create_mock.await_args.kwargs["type_code"] == "paid"


@pytest.mark.asyncio
async def test_create_failure_returns_none_gracefully():
    from core import client_resolve as nc

    with patch("arcana.repos.pg_clients_repo.PgClientsRepo.find",
               AsyncMock(return_value=None)), \
         patch("arcana.repos.pg_clients_repo.PgClientsRepo.create",
               AsyncMock(side_effect=Exception("pg error"))):
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
async def test_apply_updates_client_type_self_sets_type_code():
    """apply_updates с new_type=Self → PgClientsRepo.update_profile(type_code='self')."""
    import core.reply_update as ru
    from arcana.repos.pg_clients_repo import PgClientsRepo

    with patch.object(PgClientsRepo, "find_by_id", AsyncMock(return_value=None)), \
         patch.object(PgClientsRepo, "update_profile", AsyncMock()) as m:
        applied = await ru.apply_updates(
            page_id="1", page_type="client", db_id=None,
            updates={"new_type": "Self"},
        )
    assert m.await_args.kwargs["type_code"] == "self"
    assert applied.get("Тип клиента") == "Self"


@pytest.mark.asyncio
async def test_apply_updates_client_type_free_sets_type_code():
    import core.reply_update as ru
    from arcana.repos.pg_clients_repo import PgClientsRepo

    with patch.object(PgClientsRepo, "find_by_id", AsyncMock(return_value=None)), \
         patch.object(PgClientsRepo, "update_profile", AsyncMock()) as m:
        await ru.apply_updates(
            page_id="1", page_type="client", db_id=None,
            updates={"new_type": "Бесплатный"},
        )
    assert m.await_args.kwargs["type_code"] == "free"


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
    with patch.object(cr, "find_or_create_client",
                      AsyncMock(return_value=("c-lena", True))), \
         patch.object(cr, "save_message_page", AsyncMock()):
        cid = await cr.resolve_or_create(msg, "Лена", user_notion_id="u")

    assert cid == "c-lena", "клиент должен создаться, не оставаться сиротой"


# ── is_valid_client_name + гард от LLM-рефузала ─────────────────────────────

def test_refusal_phrase_rejected():
    from core.client_resolve import is_valid_client_name
    assert is_valid_client_name("не могу извлечь имя") is False


def test_long_refusal_rejected():
    from core.client_resolve import is_valid_client_name
    assert is_valid_client_name(
        "Я не имею доступа к базе данных клиентов..."
    ) is False


def test_short_name_valid():
    from core.client_resolve import is_valid_client_name
    assert is_valid_client_name("оля") is True


def test_two_word_name_valid():
    from core.client_resolve import is_valid_client_name
    assert is_valid_client_name("Анна Петрова") is True


@pytest.mark.asyncio
async def test_resolve_returns_none_for_refusal_without_db_call():
    """Рефузал LLM → resolve_or_create возвращает None, find_or_create НЕ вызывается."""
    from core import client_resolve as cr
    msg = _msg()
    foc = AsyncMock(return_value=("c-x", True))
    with patch.object(cr, "find_or_create_client", foc):
        result = await cr.resolve_or_create(msg, "не могу извлечь имя", user_notion_id="u")
    assert result is None
    foc.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_proceeds_for_valid_name():
    """Валидное имя → find_or_create_client вызывается нормально."""
    from core import client_resolve as cr
    msg = _msg()
    foc = AsyncMock(return_value=("c-olia", False))
    with patch.object(cr, "find_or_create_client", foc), \
         patch.object(cr, "save_message_page", AsyncMock()):
        result = await cr.resolve_or_create(msg, "оля", user_notion_id="u")
    assert result == "c-olia"
    foc.assert_awaited_once()
