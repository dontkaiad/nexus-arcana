"""tests/test_barter_prompt.py — интерактивный prompt бартера + reply-парсинг."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_pending_db(monkeypatch, tmp_path):
    from arcana.handlers import barter_prompt
    monkeypatch.setattr(barter_prompt, "_DB", str(tmp_path / "pb.db"))
    yield


def _pg_item(pid, name, group="приворот — Оля", status="not_started"):
    from core.repos.pg_nexus_lists_repo import InventoryItem
    return InventoryItem(
        id=pid,
        name=name,
        list_type="чеклист",
        status=status,
        category="🔄 Бартер",
        group_name=group,
        user_notion_id="u1",
    )


# ── 1. Pending создаётся после propose_barter_prompt ──────────────────────────

@pytest.mark.asyncio
async def test_propose_barter_prompt_saves_pending_and_asks():
    from arcana.handlers.barter_prompt import propose_barter_prompt, _load
    msg = MagicMock()
    msg.from_user.id = 11
    msg.answer = AsyncMock()
    await propose_barter_prompt(msg, kind="ritual",
                                 page_id="rit-1", group_name="приворот — Оля")
    state = _load(11)
    assert state and state["page_id"] == "rit-1"
    assert state["group_name"] == "приворот — Оля"
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Что в бартере" in text


# ── 2. Pending text → создаёт N пунктов чеклиста ──────────────────────────────

@pytest.mark.asyncio
async def test_pending_text_creates_n_checklist_items():
    from arcana.handlers import barter_prompt
    from arcana.handlers.barter_prompt import handle_pending_text, _save, _load
    _save(22, {"kind": "ritual", "page_id": "rit-2", "group_name": "приворот — Оля"})

    msg = MagicMock()
    msg.from_user.id = 22
    msg.answer = AsyncMock()

    mock_add = AsyncMock(return_value=["r1", "r2", "r3"])
    with patch.object(barter_prompt._lists_repo, "add", mock_add):
        handled = await handle_pending_text(
            msg, "2 блока сигарет, мерч улицы восток, поездка в беларусь",
            user_notion_id="u1",
        )
    assert handled is True
    mock_add.assert_awaited_once()
    items_arg, list_type_arg, bot_arg = (
        mock_add.await_args.args[0],
        mock_add.await_args.args[1],
        mock_add.await_args.args[2],
    )
    assert list_type_arg == "📋 Чеклист"
    assert bot_arg == "🌒 Arcana"
    assert len(items_arg) == 3
    for it in items_arg:
        assert it["category"] == "🔄 Бартер"
        assert it["group"] == "приворот — Оля"
    names = [it["name"] for it in items_arg]
    assert "2 блока сигарет" in names
    assert "мерч улицы восток" in names
    assert "поездка в беларусь" in names
    # state очищен
    assert _load(22) is None
    # ответ «Создано N»
    body = msg.answer.await_args.args[0]
    assert "Создано 3" in body


# ── 3. Reply «отдала блок сигарет» → fuzzy match → Done ───────────────────────

@pytest.mark.asyncio
async def test_reply_otdala_marks_done():
    from arcana.handlers.barter_prompt import handle_reply_text
    from core import list_manager as lm

    reply = MagicMock()
    reply.message_id = 555
    reply.from_user.is_bot = True

    msg = MagicMock()
    msg.chat.id = 100
    msg.from_user.id = 1
    msg.reply_to_message = reply
    msg.answer = AsyncMock()

    ritual_page = {
        "id": "rit-3",
        "properties": {
            "Название": {"title": [{"plain_text": "приворот — Оля",
                                     "text": {"content": "приворот — Оля"}}]},
        },
    }
    pg_items = [
        _pg_item("b1", "блок сигарет"),
        _pg_item("b2", "мерч улицы восток"),
    ]
    mock_up_status = AsyncMock(return_value=True)
    with patch("core.message_pages.get_message_page",
               AsyncMock(return_value={"page_id": "rit-3", "page_type": "ritual",
                                        "bot": "arcana", "created_at": 0})), \
         patch("arcana.handlers.barter_prompt.get_page",
               AsyncMock(return_value=ritual_page)), \
         patch.object(lm._arcana_repo, "get_list", AsyncMock(return_value=pg_items)), \
         patch.object(lm._arcana_repo, "update_status", mock_up_status):
        ok = await handle_reply_text(msg, "отдала блок сигарет", user_notion_id="u1")
    assert ok is True
    mock_up_status.assert_awaited_once()
    args = mock_up_status.await_args.args
    assert args[0] == "b1"  # «блок сигарет» победил
    assert args[1] == "Done"


# ── 4. Reply «вместо блока сигарет — колода таро» → rename + Done ────────────

@pytest.mark.asyncio
async def test_reply_vmesto_renames_and_marks_done():
    from arcana.handlers.barter_prompt import handle_reply_text
    from core import list_manager as lm

    reply = MagicMock()
    reply.message_id = 556
    reply.from_user.is_bot = True

    msg = MagicMock()
    msg.chat.id = 100
    msg.from_user.id = 1
    msg.reply_to_message = reply
    msg.answer = AsyncMock()

    ritual_page = {
        "id": "rit-4",
        "properties": {"Название": {"title": [{"plain_text": "приворот — Оля",
                                                "text": {"content": "приворот — Оля"}}]}},
    }
    pg_items = [_pg_item("b9", "блок сигарет")]
    mock_update = AsyncMock(return_value=True)
    with patch("core.message_pages.get_message_page",
               AsyncMock(return_value={"page_id": "rit-4", "page_type": "ritual",
                                        "bot": "arcana", "created_at": 0})), \
         patch("arcana.handlers.barter_prompt.get_page",
               AsyncMock(return_value=ritual_page)), \
         patch.object(lm._arcana_repo, "get_list", AsyncMock(return_value=pg_items)), \
         patch.object(lm._arcana_repo, "update", mock_update):
        ok = await handle_reply_text(msg, "вместо блока сигарет колода таро",
                                       user_notion_id="u1")
    assert ok is True
    mock_update.assert_awaited_once()
    args, kwargs = mock_update.await_args.args, mock_update.await_args.kwargs
    assert args[0] == "b9"
    assert kwargs.get("name") == "колода таро"
    assert kwargs.get("status") == "done"


# ── 5. Reply «закинула 1500₽» → finance_add(Доход) + закрыть деньги-пункт ────

@pytest.mark.asyncio
async def test_reply_money_creates_finance_and_closes_money_item():
    from arcana.handlers import barter_prompt
    from arcana.handlers.barter_prompt import handle_reply_text
    from core import list_manager as lm

    reply = MagicMock()
    reply.message_id = 557
    reply.from_user.is_bot = True

    msg = MagicMock()
    msg.chat.id = 100
    msg.from_user.id = 1
    msg.reply_to_message = reply
    msg.answer = AsyncMock()

    ritual_page = {
        "id": "rit-5",
        "properties": {"Название": {"title": [{"plain_text": "приворот — Оля",
                                                "text": {"content": "приворот — Оля"}}]}},
    }
    pg_items = [
        _pg_item("m1", "откуп деньгами"),
        _pg_item("b2", "блок сигарет"),
    ]
    fa = AsyncMock(return_value="fin-OK")
    mock_up_status = AsyncMock(return_value=True)
    with patch("core.message_pages.get_message_page",
               AsyncMock(return_value={"page_id": "rit-5", "page_type": "ritual",
                                        "bot": "arcana", "created_at": 0})), \
         patch("arcana.handlers.barter_prompt.get_page",
               AsyncMock(return_value=ritual_page)), \
         patch.object(lm._arcana_repo, "get_list", AsyncMock(return_value=pg_items)), \
         patch.object(lm._arcana_repo, "update_status", mock_up_status), \
         patch.object(barter_prompt._fin_repo, "add", fa):
        ok = await handle_reply_text(msg, "закинула 1500₽ за приворот",
                                       user_notion_id="u1")
    assert ok is True
    fa.assert_awaited_once()
    kw = fa.await_args.kwargs
    assert kw["amount"] == 1500.0
    assert kw["bot_label"] == "🌒 Arcana"
    assert kw["type_"] == "💰 Доход"
    # Закрыли money-пункт «откуп деньгами»
    mock_up_status.assert_awaited()
    args = mock_up_status.await_args.args
    assert args[0] == "m1"
    assert args[1] == "Done"
