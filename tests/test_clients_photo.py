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
