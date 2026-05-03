"""tests/test_clients_photo.py — фото клиентов через TG бота.

Покрываем:
 1. /client_photo переводит в await_name.
 2. await_name + текст → find_or_create_client → await_photo.
 3. await_photo + фото → cloudinary_upload + update_page("Фото", url).
 4. Reply на bot-сообщение клиента → await_confirm + ✅ → upload.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_pending_dbs(monkeypatch, tmp_path):
    """Прокидываем pending sqlite в tmp_path, чтобы тесты не лапали реальную БД."""
    import importlib

    from arcana import pending_client_photo as pcp

    db_file = tmp_path / "pending_client_photo.db"
    monkeypatch.setattr(pcp, "DB_PATH", str(db_file))
    yield


@pytest.mark.asyncio
async def test_command_sets_await_name():
    from arcana.handlers.client_photo import cmd_client_photo
    from arcana.pending_client_photo import get as get_pending

    msg = MagicMock()
    msg.from_user.id = 42
    msg.chat.id = 100
    msg.reply_to_message = None
    msg.answer = AsyncMock()
    await cmd_client_photo(msg)
    state = await get_pending(42)
    assert state and state["step"] == "await_name"
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_await_name_creates_client_and_advances():
    from arcana.handlers.client_photo import handle_pending_text
    from arcana.pending_client_photo import get as get_pending, save as save_pending

    await save_pending(42, {"step": "await_name"})
    msg = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock()
    with patch("arcana.handlers.client_photo.find_or_create_client",
               AsyncMock(return_value="cli-1")):
        handled = await handle_pending_text(msg, "Маша")
    assert handled is True
    state = await get_pending(42)
    assert state["step"] == "await_photo"
    assert state["client_id"] == "cli-1"
    assert state["client_name"] == "Маша"


@pytest.mark.asyncio
async def test_await_photo_uploads_and_writes_notion():
    from arcana.handlers.client_photo import handle_pending_photo
    from arcana.pending_client_photo import get as get_pending, save as save_pending

    await save_pending(7, {
        "step": "await_photo",
        "client_id": "cli-7777",
        "client_name": "Маша",
    })

    photo = MagicMock()
    photo.file_id = "tg-file-id"

    file_obj = MagicMock()
    file_obj.file_path = "photos/cli.jpg"

    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=file_obj)
    bio = BytesIO(b"fakejpgbytes")
    bot.download_file = AsyncMock(return_value=bio)

    msg = MagicMock()
    msg.from_user.id = 7
    msg.chat.id = 100
    msg.bot = bot
    msg.photo = [photo]
    msg.reply_to_message = None
    msg.answer = AsyncMock()

    with patch("arcana.handlers.client_photo.cloudinary_upload",
               AsyncMock(return_value="https://res.cloudinary.com/x/y.jpg")) as cu, \
         patch("arcana.handlers.client_photo.update_page",
               AsyncMock(return_value=None)) as up:
        handled = await handle_pending_photo(msg)
    assert handled is True
    cu.assert_awaited_once()
    assert cu.await_args.kwargs["folder"] == "arcana-clients"
    up.assert_awaited_once()
    args = up.await_args.args
    assert args[0] == "cli-7777"
    assert args[1] == {"Фото": {"url": "https://res.cloudinary.com/x/y.jpg"}}
    # state очищен
    assert await get_pending(7) is None


@pytest.mark.asyncio
async def test_create_client_with_photo_in_one_message():
    """text+photo за один сабмит: handle_add_client создаёт клиента и
    auto-attach помещает Cloudinary URL в Notion поле «Фото»."""
    from arcana.handlers import clients as cmod

    photo = MagicMock()
    photo.file_id = "tg-cap-photo"

    file_obj = MagicMock()
    file_obj.file_path = "p/x.jpg"
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=file_obj)
    bot.download_file = AsyncMock(return_value=BytesIO(b"j"))

    bot_msg = MagicMock()
    bot_msg.chat.id = 111
    bot_msg.message_id = 222

    msg = MagicMock()
    msg.from_user.id = 17
    msg.chat.id = 111
    msg.bot = bot
    msg.photo = [photo]
    msg.answer = AsyncMock(return_value=bot_msg)

    with patch.object(cmod, "ask_claude",
                      AsyncMock(return_value='{"name":"Маша","contact":"@m","request":"финансы"}')), \
         patch.object(cmod, "client_find", AsyncMock(return_value=None)), \
         patch.object(cmod, "client_add", AsyncMock(return_value="cli-new-1")), \
         patch("arcana.handlers.client_photo.cloudinary_upload",
               AsyncMock(return_value="https://cdn/x.jpg")) as cu, \
         patch("arcana.handlers.client_photo.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("arcana.handlers.clients.save_pending_client", AsyncMock(return_value=None), create=True):
        await cmod.handle_add_client(msg, "новый клиент Маша, тема финансы")

    cu.assert_awaited_once()
    assert cu.await_args.kwargs["folder"] == "arcana-clients"
    up.assert_awaited_once()
    assert up.await_args.args[0] == "cli-new-1"
    assert up.await_args.args[1] == {"Фото": {"url": "https://cdn/x.jpg"}}


@pytest.mark.asyncio
async def test_reply_with_photo_in_60s_skips_confirmation(tmp_path, monkeypatch):
    """Reply фото на сообщение бота с свежим (<60s) page_type='client' →
    сразу attach без подтверждения."""
    import time as _time
    from arcana.handlers.client_photo import handle_pending_photo

    photo = MagicMock()
    photo.file_id = "tg-reply-photo"

    file_obj = MagicMock()
    file_obj.file_path = "p/r.jpg"
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=file_obj)
    bot.download_file = AsyncMock(return_value=BytesIO(b"j"))

    reply_msg = MagicMock()
    reply_msg.message_id = 555
    reply_msg.from_user.is_bot = True

    msg = MagicMock()
    msg.from_user.id = 19
    msg.chat.id = 111
    msg.bot = bot
    msg.reply_to_message = reply_msg
    msg.photo = [photo]
    msg.answer = AsyncMock()

    with patch("arcana.handlers.client_photo.get_message_page",
               AsyncMock(return_value={
                   "page_id": "cli-fresh",
                   "page_type": "client",
                   "bot": "arcana",
                   "created_at": _time.time() - 10,  # 10 сек назад
               })), \
         patch("arcana.handlers.client_photo.cloudinary_upload",
               AsyncMock(return_value="https://cdn/r.jpg")) as cu, \
         patch("arcana.handlers.client_photo.update_page",
               AsyncMock(return_value=None)) as up:
        handled = await handle_pending_photo(msg)
    assert handled is True
    cu.assert_awaited_once()
    up.assert_awaited_once()
    assert up.await_args.args[0] == "cli-fresh"
    # подтверждения не было
    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_reply_with_photo_after_60s_still_asks_confirmation():
    """Старая ветка с подтверждением: создание давно (>60s) → запрос подтверждения."""
    import time as _time
    from arcana.handlers.client_photo import handle_pending_photo

    photo = MagicMock()
    photo.file_id = "tg-old-photo"

    reply_msg = MagicMock()
    reply_msg.message_id = 777
    reply_msg.from_user.is_bot = True

    msg = MagicMock()
    msg.from_user.id = 21
    msg.chat.id = 111
    msg.reply_to_message = reply_msg
    msg.photo = [photo]
    msg.answer = AsyncMock()

    with patch("arcana.handlers.client_photo.get_message_page",
               AsyncMock(return_value={
                   "page_id": "cli-old",
                   "page_type": "client",
                   "bot": "arcana",
                   "created_at": _time.time() - 600,  # 10 минут назад
               })):
        handled = await handle_pending_photo(msg)
    assert handled is True
    # Подтверждение запрошено
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_reply_with_photo_starts_confirm_flow():
    from arcana.handlers.client_photo import handle_pending_photo
    from arcana.pending_client_photo import get as get_pending

    photo = MagicMock()
    photo.file_id = "tg-file-id"

    reply_msg = MagicMock()
    reply_msg.message_id = 555
    reply_msg.from_user.is_bot = True

    msg = MagicMock()
    msg.from_user.id = 9
    msg.chat.id = 100
    msg.reply_to_message = reply_msg
    msg.photo = [photo]
    msg.answer = AsyncMock()

    with patch("arcana.handlers.client_photo.get_message_page",
               AsyncMock(return_value={"page_id": "cli-9999", "page_type": "client"})):
        handled = await handle_pending_photo(msg)
    assert handled is True
    state = await get_pending(9)
    assert state and state["step"] == "await_confirm"
    assert state["client_id"] == "cli-9999"
    assert state["file_id"] == "tg-file-id"
    msg.answer.assert_awaited()
