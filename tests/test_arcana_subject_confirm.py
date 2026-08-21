"""tests/test_arcana_subject_confirm.py — диалог подтверждения subject_id (#189).

_maybe_prompt_subject_match: находит совпадение в core.memory → шлёт inline-
подтверждение и сохраняет pending; не находит → молчит, ничего не меняет.

cb_subject_confirm_yes/no: разбирают pending по slug, «Да» проставляет
subject_id на все page_ids этой отправки, «Нет» — оставляет NULL как было.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.handlers import sessions as sess_mod


class _Mem:
    def __init__(self, id="777", fact="Вадим — бывший, тревожит", related_to="вадим"):
        self.id = id
        self.fact = fact
        self.related_to = related_to


@pytest.mark.asyncio
async def test_prompt_sent_when_memory_match_found(mock_message):
    msg = mock_message()
    with patch("core.memory.find_memories_by_subject_name",
               AsyncMock(return_value=[_Mem()])), \
         patch("arcana.pending_tarot.save_pending", AsyncMock()) as save_pending:
        await sess_mod._maybe_prompt_subject_match(
            msg, 67686090,
            subject_name="Вадим", session_name="Вадим — диагностика",
            client_id=None, user_notion_id="u-1", page_ids=["1", "2"],
        )

    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Вадим" in text
    save_pending.assert_awaited_once()
    state = save_pending.await_args.args[1]
    assert state["type"] == "subject_confirm_pending"
    assert state["memory_id"] == "777"
    assert state["page_ids"] == ["1", "2"]


@pytest.mark.asyncio
async def test_no_prompt_when_no_memory_match(mock_message):
    msg = mock_message()
    with patch("core.memory.find_memories_by_subject_name",
               AsyncMock(return_value=[])), \
         patch("arcana.pending_tarot.save_pending", AsyncMock()) as save_pending:
        await sess_mod._maybe_prompt_subject_match(
            msg, 67686090,
            subject_name="Незнакомец", session_name="Незнакомец — тема",
            client_id=None, user_notion_id="u-1", page_ids=["1"],
        )

    msg.answer.assert_not_awaited()
    save_pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_yes_sets_subject_on_all_page_ids(mock_callback):
    cb = mock_callback(data="subj_yes:abc123")
    pending = {
        "type": "subject_confirm_pending", "slug": "abc123",
        "memory_id": "777", "subject_name": "Вадим",
        "page_ids": ["1", "2"],
    }
    set_subject = AsyncMock(return_value=2)
    with patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=pending)), \
         patch("arcana.pending_tarot.delete_pending", AsyncMock()) as delete_pending, \
         patch.object(sess_mod._repo, "set_subject", set_subject):
        await sess_mod.cb_subject_confirm_yes(cb)

    set_subject.assert_awaited_once_with(["1", "2"], 777)
    delete_pending.assert_awaited_once_with(cb.from_user.id)
    cb.message.answer.assert_awaited_once()
    assert "Вадим" in cb.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_confirm_yes_ignores_mismatched_slug(mock_callback):
    cb = mock_callback(data="subj_yes:wrong-slug")
    pending = {
        "type": "subject_confirm_pending", "slug": "abc123",
        "memory_id": "777", "subject_name": "Вадим", "page_ids": ["1"],
    }
    set_subject = AsyncMock()
    with patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=pending)), \
         patch.object(sess_mod._repo, "set_subject", set_subject):
        await sess_mod.cb_subject_confirm_yes(cb)

    set_subject.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_no_leaves_subject_untouched(mock_callback):
    cb = mock_callback(data="subj_no:abc123")
    pending = {
        "type": "subject_confirm_pending", "slug": "abc123",
        "memory_id": "777", "subject_name": "Вадим", "page_ids": ["1"],
    }
    set_subject = AsyncMock()
    with patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=pending)), \
         patch("arcana.pending_tarot.delete_pending", AsyncMock()) as delete_pending, \
         patch.object(sess_mod._repo, "set_subject", set_subject):
        await sess_mod.cb_subject_confirm_no(cb)

    set_subject.assert_not_awaited()
    delete_pending.assert_awaited_once_with(cb.from_user.id)
    cb.message.answer.assert_awaited_once_with("Ок, отдельная тема.")
