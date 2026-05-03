"""tests/test_barter_prompt.py — интерактивный prompt бартера + reply-парсинг."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_pending_db(monkeypatch, tmp_path):
    from arcana.handlers import barter_prompt
    monkeypatch.setattr(barter_prompt, "_DB", str(tmp_path / "pb.db"))
    yield


def _list_item(pid: str, name: str, status: str = "Not started") -> dict:
    return {
        "id": pid,
        "properties": {
            "Название": {"title": [{"plain_text": name, "text": {"content": name}}]},
            "Статус": {"status": {"name": status}},
            "Категория": {"select": {"name": "🔄 Бартер"}},
            "Тип": {"select": {"name": "📋 Чеклист"}},
            "Группа": {"rich_text": [{"plain_text": "приворот — Оля",
                                       "text": {"content": "приворот — Оля"}}]},
        },
    }


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
    from arcana.handlers.barter_prompt import handle_pending_text, _save, _load
    _save(22, {"kind": "ritual", "page_id": "rit-2", "group_name": "приворот — Оля"})

    msg = MagicMock()
    msg.from_user.id = 22
    msg.answer = AsyncMock()

    pc = AsyncMock(side_effect=lambda db, props: f"new-{props['Название']['title'][0]['text']['content']}")
    with patch("arcana.handlers.barter_prompt.page_create", pc):
        handled = await handle_pending_text(
            msg, "2 блока сигарет, мерч улицы восток, поездка в беларусь",
            user_notion_id="u1",
        )
    assert handled is True
    assert pc.await_count == 3
    # Все три созданы с правильными полями
    for call in pc.await_args_list:
        props = call.args[1]
        assert props["Тип"]["select"]["name"] == "📋 Чеклист"
        assert props["Категория"]["select"]["name"] == "🔄 Бартер"
        assert props["Бот"]["select"]["name"] == "🌒 Arcana"
        assert props["Группа"]["rich_text"][0]["text"]["content"] == "приворот — Оля"
    # state очищен
    assert _load(22) is None
    # ответ «Создано N»
    body = msg.answer.await_args.args[0]
    assert "Создано 3" in body


# ── 3. Reply «отдала блок сигарет» → fuzzy match → Done ───────────────────────

@pytest.mark.asyncio
async def test_reply_otdala_marks_done():
    from arcana.handlers.barter_prompt import handle_reply_text

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
    items = [
        _list_item("b1", "блок сигарет"),
        _list_item("b2", "мерч улицы восток"),
    ]
    with patch("core.message_pages.get_message_page",
               AsyncMock(return_value={"page_id": "rit-3", "page_type": "ritual",
                                        "bot": "arcana", "created_at": 0})), \
         patch("arcana.handlers.barter_prompt.get_page",
               AsyncMock(return_value=ritual_page)), \
         patch("arcana.handlers.barter_prompt.query_pages",
               AsyncMock(return_value=items)), \
         patch("arcana.handlers.barter_prompt.update_page",
               AsyncMock(return_value=None)) as up:
        ok = await handle_reply_text(msg, "отдала блок сигарет", user_notion_id="u1")
    assert ok is True
    up.assert_awaited_once()
    args = up.await_args.args
    assert args[0] == "b1"  # «блок сигарет» победил
    assert args[1]["Статус"]["status"]["name"] == "Done"


# ── 4. Reply «вместо блока сигарет — колода таро» → rename + Done ────────────

@pytest.mark.asyncio
async def test_reply_vmesto_renames_and_marks_done():
    from arcana.handlers.barter_prompt import handle_reply_text

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
    items = [_list_item("b9", "блок сигарет")]
    with patch("core.message_pages.get_message_page",
               AsyncMock(return_value={"page_id": "rit-4", "page_type": "ritual",
                                        "bot": "arcana", "created_at": 0})), \
         patch("arcana.handlers.barter_prompt.get_page",
               AsyncMock(return_value=ritual_page)), \
         patch("arcana.handlers.barter_prompt.query_pages",
               AsyncMock(return_value=items)), \
         patch("arcana.handlers.barter_prompt.update_page",
               AsyncMock(return_value=None)) as up:
        ok = await handle_reply_text(msg, "вместо блока сигарет колода таро",
                                       user_notion_id="u1")
    assert ok is True
    args = up.await_args.args
    assert args[0] == "b9"
    written = args[1]
    assert written["Статус"]["status"]["name"] == "Done"
    new_title = written["Название"]["title"][0]["text"]["content"]
    assert new_title == "колода таро"


# ── 5. Reply «закинула 1500₽» → finance_add(Доход) + закрыть деньги-пункт ────

@pytest.mark.asyncio
async def test_reply_money_creates_finance_and_closes_money_item():
    from arcana.handlers.barter_prompt import handle_reply_text

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
    items = [
        _list_item("m1", "откуп деньгами"),
        _list_item("b2", "блок сигарет"),
    ]
    fa = AsyncMock(return_value="fin-OK")
    with patch("core.message_pages.get_message_page",
               AsyncMock(return_value={"page_id": "rit-5", "page_type": "ritual",
                                        "bot": "arcana", "created_at": 0})), \
         patch("arcana.handlers.barter_prompt.get_page",
               AsyncMock(return_value=ritual_page)), \
         patch("arcana.handlers.barter_prompt.query_pages",
               AsyncMock(return_value=items)), \
         patch("arcana.handlers.barter_prompt.update_page",
               AsyncMock(return_value=None)) as up, \
         patch("arcana.handlers.barter_prompt.finance_add", fa):
        ok = await handle_reply_text(msg, "закинула 1500₽ за приворот",
                                       user_notion_id="u1")
    assert ok is True
    fa.assert_awaited_once()
    kw = fa.await_args.kwargs
    assert kw["amount"] == 1500.0
    assert kw["bot_label"] == "🌒 Arcana"
    assert kw["type_"] == "💰 Доход"
    # Закрыли money-пункт «откуп деньгами»
    up.assert_awaited()
    assert up.await_args.args[0] == "m1"
    assert up.await_args.args[1]["Статус"]["status"]["name"] == "Done"
