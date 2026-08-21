"""tests/test_memory_move_to_notes.py — reply "это в заметки" на плашку 🧠
Память переносит факт в 📝 Заметки (#188), а не падает в classify()
{"type":"unknown"}.

Privacy: generic placeholder-факт, никаких реальных данных.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mk_reply_message(reply_text: str, orig_text: str, chat_id: int = 1, orig_msg_id: int = 10):
    orig = MagicMock()
    orig.text = orig_text
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


# ── nexus: полный перенос ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nexus_move_to_notes_archives_memory_and_creates_note():
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message(
        "это в заметки", "🧠 Запомнил [🐾 Коты]: S7 хорошо летать с животными до 23 кг",
    )
    mapping = {"page_id": "99", "page_type": "memory", "bot": "nexus"}
    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True, "fact": None, "category": None})), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock(return_value="note-1")) as note_add, \
         patch("core.repos.memory_repo.MemoryRepo.archive", AsyncMock(return_value=True)) as archive:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    note_add.assert_awaited_once()
    assert note_add.await_args.kwargs["text"] == "S7 хорошо летать с животными до 23 кг"
    archive.assert_awaited_once_with("99")
    msg.answer.assert_any_call("📝 Перенесено в заметки: S7 хорошо летать с животными до 23 кг")


@pytest.mark.asyncio
async def test_nexus_move_to_notes_plaque_regex_no_category():
    """Плашка без категории («🧠 Запомнил: X») тоже должна распознаваться."""
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("это в заметки", "🧠 Запомнил: голая фраза без категории")
    mapping = {"page_id": "5", "page_type": "memory", "bot": "nexus"}
    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock(return_value="note-2")) as note_add, \
         patch("core.repos.memory_repo.MemoryRepo.archive", AsyncMock(return_value=True)):
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    assert note_add.await_args.kwargs["text"] == "голая фраза без категории"


@pytest.mark.asyncio
async def test_nexus_move_to_notes_note_create_failure_reports_error():
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("это в заметки", "🧠 Запомнил [🐾 Коты]: факт")
    mapping = {"page_id": "99", "page_type": "memory", "bot": "nexus"}
    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})), \
         patch("nexus.repos.notes_repo.NotesRepo.add", AsyncMock(return_value=None)), \
         patch("core.repos.memory_repo.MemoryRepo.archive", AsyncMock()) as archive:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    archive.assert_not_called()  # не архивируем факт, если заметка не создалась
    msg.answer.assert_any_call("⚠️ Не получилось создать заметку.")


@pytest.mark.asyncio
async def test_nexus_non_memory_move_flag_falls_through_to_field_update():
    """move_to_notes=False (обычный случай) — не должно мешать нормальному
    field-апдейту (fact/category)."""
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("исправь: другой текст", "🧠 Запомнил [🐾 Коты]: факт")
    mapping = {"page_id": "99", "page_type": "memory", "bot": "nexus"}
    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": False, "fact": "другой текст"})), \
         patch("core.repos.memory_repo.MemoryRepo.update_fields", AsyncMock(return_value=True)) as upd:
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    upd.assert_awaited_once_with("99", fact="другой текст", category=None)


# ── arcana: graceful — нет заметок ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_arcana_move_to_notes_graceful_no_notes_feature():
    import arcana.handlers.reply_update as aru
    msg, orig = _mk_reply_message("это в заметки", "🧠 Запомнил [🐾 Коты]: факт")
    mapping = {"page_id": "99", "page_type": "memory", "bot": "arcana"}
    with patch("arcana.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("core.shared_handlers.get_user_tz", AsyncMock(return_value=3)), \
         patch("arcana.handlers.reply_update.parse_reply", AsyncMock(return_value={"move_to_notes": True})):
        handled = await aru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    msg.answer.assert_any_call("📝 В Arcana заметок нет — могу поправить только сам факт/категорию.")
