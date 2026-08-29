"""tests/test_list_reply_add_item.py — reply "и ещё соль" на плашку
«Добавлено в покупки» добавляет позицию в тот же список (по образцу #192
move_to_notes, tests/test_list_move_to_notes.py). Раньше такой reply не
понимался вообще: Haiku возвращал только move_to_notes=false, updates
пустел, юзер получал «✏️ Не поняла что дополнить».

Privacy: generic placeholder-товары, никаких реальных данных.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.reply_update import _apply_list


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


# ── _apply_list: unit-уровень ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_list_adds_item_with_inherited_category_and_type():
    item = SimpleNamespace(
        id="42", name="Молоко", list_type="покупки", category="🍜 Продукты",
        group_name="", user_notion_id="u1",
    )
    with patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(
             return_value=[{"id": "99", "name": "Соль"}])) as add:
        applied = await _apply_list("42", {"add_item": "Соль"})

    assert applied == {"Добавлено": "Соль"}
    add.assert_awaited_once_with(
        [{"name": "Соль", "category": "🍜 Продукты", "group": ""}],
        "🛒 Покупки", "☀️ Nexus", "u1",
    )


@pytest.mark.asyncio
async def test_apply_list_falls_back_to_arcana_repo():
    item = SimpleNamespace(
        id="7", name="Свеча", list_type="инвентарь", category="🕯️ Расходники",
        group_name="", user_notion_id="u1",
    )
    with patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=None)), \
         patch("core.list_manager._arcana_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(
             return_value=[{"id": "100", "name": "Спички"}])) as add:
        applied = await _apply_list("7", {"add_item": "Спички"})

    assert applied == {"Добавлено": "Спички"}
    args, _ = add.await_args
    assert args[2] == "🌒 Arcana"


@pytest.mark.asyncio
async def test_apply_list_no_add_item_field_is_noop():
    with patch("core.list_manager._nexus_repo.get_by_id", AsyncMock()) as get_by_id:
        applied = await _apply_list("42", {})
    assert applied == {}
    get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_list_item_not_found_anywhere_returns_empty():
    with patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=None)), \
         patch("core.list_manager._arcana_repo.get_by_id", AsyncMock(return_value=None)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock()) as add:
        applied = await _apply_list("999", {"add_item": "Соль"})
    assert applied == {}
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_list_add_failure_returns_empty():
    item = SimpleNamespace(
        id="42", name="Молоко", list_type="покупки", category="🍜 Продукты",
        group_name="", user_notion_id="u1",
    )
    with patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(return_value=[])):
        applied = await _apply_list("42", {"add_item": "Соль"})
    assert applied == {}


# ── handle_reply_update: интеграция с диспатчем (по образцу test_list_move_to_notes.py) ──

@pytest.mark.asyncio
async def test_reply_add_item_end_to_end_confirms_to_user():
    import nexus.handlers.reply_update as ru
    msg, orig = _mk_reply_message("и ещё соль")
    mapping = {"page_id": "42", "page_type": "list", "bot": "nexus"}
    item = SimpleNamespace(
        id="42", name="Молоко", list_type="покупки", category="🍜 Продукты",
        group_name="", user_notion_id="u1",
    )

    with patch("nexus.handlers.reply_update.get_message_page", AsyncMock(return_value=mapping)), \
         patch("nexus.handlers.reply_update.parse_reply", AsyncMock(
             return_value={"move_to_notes": False, "add_item": "Соль"})), \
         patch("core.list_manager._nexus_repo.get_by_id", AsyncMock(return_value=item)), \
         patch("core.repos.lists_repo.ListsRepo.add", AsyncMock(
             return_value=[{"id": "99", "name": "Соль"}])), \
         patch("nexus.handlers.reply_update.react", AsyncMock()):
        handled = await ru.handle_reply_update(msg, user_notion_id="u1")

    assert handled is True
    msg.answer.assert_any_call("✏️ Дополнено:\n  • Добавлено: Соль")
