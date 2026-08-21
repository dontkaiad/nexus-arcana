"""tests/test_list_move_to_notes.py — reply "отправь в заметки" на плашку
«Добавлено в покупки» переносит позицию в 📝 Заметки (#192), по образцу #188
(tests/test_memory_move_to_notes.py) для 🧠 Памяти.

Privacy: generic placeholder-товар, никаких реальных данных.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mk_reply_message(reply_text: str, chat_id: int = 1, orig_msg_id: int = 10):
    orig = MagicMock()
    orig.text = "🛒 <b>Добавлено в покупки</b>:\n  • Молоко"
    orig.caption = None
    orig.message_id = orig_msg_id

    msg = MagicMock()
    msg.reply_to_message = orig
    msg.text = reply_text
    msg.caption = None
    msg.chat.id = chat_id
    msg.from_user.id = chat_id
    msg.answer = AsyncMock()
    return msg, orig


# ── #192: reply на «Добавлено в покупки» → перенос в заметки ────────────────

@pytest.mark.asyncio
async def test_list_move_to_notes_archives_item_and_creates_note():
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("отправь в заметки")
    mapping = {"page_id": "42", "page_type": "list", "bot": "nexus"}
    item = SimpleNamespace(id="42", name="Молоко")

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})), \
         patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock(return_value="note-1")) as note_add, \
         patch("core.repos.lists_repo.ListsRepo.archive", AsyncMock(return_value=1)) as archive:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    note_add.assert_awaited_once()
    assert note_add.await_args.kwargs["text"] == "Молоко"
    archive.assert_awaited_once_with(["42"])
    msg.answer.assert_any_call("📝 Перенесено в заметки: Молоко")


@pytest.mark.asyncio
async def test_list_move_to_notes_falls_back_to_arcana_repo():
    """Item не нашёлся в nexus_lists → пробуем arcana_inventory (тот же
    fallback-паттерн, что у archive_items/mark_items_done в list_manager)."""
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("отправь в заметки")
    mapping = {"page_id": "7", "page_type": "list", "bot": "nexus"}
    item = SimpleNamespace(id="7", name="Свеча ритуальная")

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})), \
         patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=None)), \
         patch("core.list_manager._arcana_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock(return_value="note-2")) as note_add, \
         patch("core.repos.lists_repo.ListsRepo.archive", AsyncMock(return_value=1)) as archive:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    assert note_add.await_args.kwargs["text"] == "Свеча ритуальная"
    archive.assert_awaited_once_with(["7"])


@pytest.mark.asyncio
async def test_list_move_to_notes_note_create_failure_reports_error():
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("отправь в заметки")
    mapping = {"page_id": "42", "page_type": "list", "bot": "nexus"}
    item = SimpleNamespace(id="42", name="Молоко")

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})), \
         patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock(return_value=None)), \
         patch("core.repos.lists_repo.ListsRepo.archive", AsyncMock()) as archive:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    archive.assert_not_called()  # не архивируем item, если заметка не создалась
    msg.answer.assert_any_call("⚠️ Не получилось создать заметку.")


@pytest.mark.asyncio
async def test_list_item_not_found_anywhere_falls_through():
    """page_id не резолвится ни в nexus_lists, ни в arcana_inventory (запись
    успела уйти, например) → False → обычный field-update путь ("нечего
    дополнить", т.к. у list сегодня нет других полей в промпте)."""
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("отправь в заметки")
    mapping = {"page_id": "999", "page_type": "list", "bot": "nexus"}

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})), \
         patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=None)), \
         patch("core.list_manager._arcana_repo.get_by_id", AsyncMock(return_value=None)), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock()) as note_add:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    note_add.assert_not_awaited()
    msg.answer.assert_any_call("✏️ Не поняла что дополнить.")


@pytest.mark.asyncio
async def test_list_move_flag_false_falls_through_to_field_update():
    """move_to_notes=False (обычный случай) не должен мешать обычному
    ответу — у list сегодня нет других полей, поэтому "нечего дополнить"."""
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("угу")
    mapping = {"page_id": "42", "page_type": "list", "bot": "nexus"}

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": False})), \
         patch("core.list_manager._nexus_repo.get_by_id", AsyncMock()) as get_by_id:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    get_by_id.assert_not_awaited()  # move_to_notes=False → _move_list_item_to_notes не вызывается
    msg.answer.assert_any_call("✏️ Не поняла что дополнить.")


# ── handle_list_buy: регистрация маппинга на ПОСЛЕДНИЙ созданный item ───────

@pytest.mark.asyncio
async def test_handle_list_buy_registers_last_created_item_for_reply():
    from nexus.handlers import lists as nx

    parsed_items = [
        {"name": "Молоко", "category": "💳 Прочее", "price_plan": None,
         "source": None, "stage": None, "group": None, "note": None,
         "priority": None, "qty": None, "expires": None},
        {"name": "Яйца", "category": "💳 Прочее", "price_plan": None,
         "source": None, "stage": None, "group": None, "note": None,
         "priority": None, "qty": None, "expires": None},
    ]
    sent_msg = MagicMock()
    sent_msg.chat.id = 555
    sent_msg.message_id = 777

    msg = MagicMock()
    msg.text = "купить молоко и яйца"
    msg.answer = AsyncMock(return_value=sent_msg)

    with patch.object(nx, "react", AsyncMock()), \
         patch.object(nx._repo, "search_memory_categories", AsyncMock(return_value={})), \
         patch.object(nx, "parse_buy_text", AsyncMock(return_value=parsed_items)), \
         patch.object(nx._repo, "get", AsyncMock(return_value=[])), \
         patch.object(nx._repo, "add", AsyncMock(return_value=[
             {"id": "item-1", "name": "Молоко", "category": "💳 Прочее"},
             {"id": "item-2", "name": "Яйца", "category": "💳 Прочее"},
         ])), \
         patch("core.message_pages.save_message_page", AsyncMock()) as save_mp:
        await nx.handle_list_buy(msg, {"text": msg.text}, user_notion_id="u1")

    save_mp.assert_awaited_once_with(
        chat_id=555, message_id=777, page_id="item-2", page_type="list", bot="nexus",
    )


@pytest.mark.asyncio
async def test_handle_list_buy_no_registration_when_nothing_created():
    """Все позиции — дубли/к Аркане → created пуст → save_message_page не
    вызывается (нечего мапить)."""
    from nexus.handlers import lists as nx

    parsed_items = [{
        "name": "Молоко", "category": "💳 Прочее", "price_plan": None,
        "source": None, "stage": None, "group": None, "note": None,
        "priority": None, "qty": None, "expires": None,
    }]
    msg = MagicMock()
    msg.text = "купить молоко"
    msg.answer = AsyncMock()

    with patch.object(nx, "react", AsyncMock()), \
         patch.object(nx._repo, "search_memory_categories", AsyncMock(return_value={})), \
         patch.object(nx, "parse_buy_text", AsyncMock(return_value=parsed_items)), \
         patch.object(nx._repo, "get", AsyncMock(return_value=[
             {"name": "Молоко"},
         ])), \
         patch("core.message_pages.save_message_page", AsyncMock()) as save_mp:
        await nx.handle_list_buy(msg, {"text": msg.text}, user_notion_id="u1")

    save_mp.assert_not_awaited()
