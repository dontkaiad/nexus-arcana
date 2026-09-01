"""tests/test_task_note_deferred_expense.py

Отложенная трата: задача с будущим дедлайном, упоминающая деньги
(«прийти к нотариусу в среду, оплата 1250»), НЕ порождает expense в момент
создания — сырое упоминание кладётся в tasks.note, а списывается реальной
finance-транзакцией только при выполнении задачи.

Покрытие:
- build_system() несёт правило + контрпримеры (task+note vs expense);
- classify() сохраняет ключ "note" из ответа Haiku, не плодит лишний item;
- _do_save_task пишет prop «Заметка» когда note есть;
- Task.note читается из PG-строки;
- handle_task_done: note с суммой → finance-транзакция + строка «Записал расход»;
- handle_task_done: note без суммы → задача закрывается, транзакции нет;
- регресс: задача без note → поведение прежнее.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.classifier as clf
import nexus.handlers.tasks as tasks_mod
import nexus.repos.pg_tasks_repo as pg_tasks_repo
from nexus.repos.pg_tasks_repo import Task


# ── 1. Промпт ────────────────────────────────────────────────────────────────

def test_build_system_has_future_money_note_rule():
    sys = clf.build_system(3)
    assert '"note"' in sys
    assert "оплата 1250" in sys          # контрпример-задача
    assert "кофе 180" in sys             # контрпример-расход
    assert "заплатила 1250" in sys       # прошедшее время → expense


# ── 2. classify сохраняет note, не плодит expense ────────────────────────────

@pytest.mark.asyncio
async def test_classify_future_task_with_money_is_single_task_with_note():
    fake = (
        '{"type":"task","title":"прийти к нотариусу","category":"💳 Прочее",'
        '"priority":"Срочно","deadline":"2026-09-03","reminder":null,'
        '"repeat":"Нет","repeat_time":null,"day_of_week":null,'
        '"note":"оплата 1250 рублей","confidence":"high"}'
    )
    with patch.object(clf, "ask_claude", AsyncMock(return_value=fake)):
        res = await clf.classify("прийти к нотариусу в среду, оплата 1250")
    assert len(res) == 1
    assert res[0]["type"] == "task"
    assert res[0]["note"] == "оплата 1250 рублей"
    assert not any(i.get("type") == "expense" for i in res)


@pytest.mark.asyncio
async def test_classify_plain_expense_regression():
    fake = ('{"type":"expense","amount":180.0,"title":"кофе","category":"🍜 Продукты",'
            '"source":"💳 Карта","confidence":"high"}')
    with patch.object(clf, "ask_claude", AsyncMock(return_value=fake)):
        res = await clf.classify("потратила 180 на кофе")
    assert len(res) == 1 and res[0]["type"] == "expense" and res[0]["amount"] == 180.0


# ── 3. _do_save_task пишет prop «Заметка» ────────────────────────────────────

@pytest.mark.asyncio
async def test_do_save_task_writes_note_prop(mock_message):
    msg = mock_message(text="прийти к нотариусу")
    created = {}

    async def _fake_create(_db, props):
        created.update(props)
        return "t99"

    with patch.object(tasks_mod._repo, "create", AsyncMock(side_effect=_fake_create)), \
         patch.object(pg_tasks_repo, "_ensure_lookups", MagicMock()), \
         patch.object(tasks_mod, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks_mod, "ask_claude", AsyncMock(return_value="")), \
         patch.object(tasks_mod, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks_mod, "_schedule_deadline_check", AsyncMock()), \
         patch("core.message_pages.save_message_page", AsyncMock()), \
         patch.object(tasks_mod, "react", AsyncMock()):
        await tasks_mod._do_save_task(
            msg,
            {"title": "прийти к нотариусу", "category": "💳 Прочее",
             "priority": "Срочно", "deadline": "2026-09-03",
             "note": "оплата 1250 рублей"},
            chat_id=msg.chat.id, uid=msg.from_user.id,
        )

    assert "Заметка" in created
    assert created["Заметка"]["rich_text"][0]["text"]["content"] == "оплата 1250 рублей"


@pytest.mark.asyncio
async def test_do_save_task_no_note_no_prop(mock_message):
    msg = mock_message(text="купить корм")
    created = {}

    async def _fake_create(_db, props):
        created.update(props)
        return "t100"

    with patch.object(tasks_mod._repo, "create", AsyncMock(side_effect=_fake_create)), \
         patch.object(pg_tasks_repo, "_ensure_lookups", MagicMock()), \
         patch.object(tasks_mod, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks_mod, "ask_claude", AsyncMock(return_value="")), \
         patch.object(tasks_mod, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks_mod, "_schedule_deadline_check", AsyncMock()), \
         patch("core.message_pages.save_message_page", AsyncMock()), \
         patch.object(tasks_mod, "react", AsyncMock()):
        await tasks_mod._do_save_task(
            msg, {"title": "купить корм", "category": "🐾 Коты", "priority": "Важно"},
            chat_id=msg.chat.id, uid=msg.from_user.id,
        )

    assert "Заметка" not in created


# ── 4. Task.note ────────────────────────────────────────────────────────────

def test_task_dataclass_has_note_default():
    assert Task(id="1", title="x").note == ""
    assert Task(id="1", title="x", note="оплата 500").note == "оплата 500"


# ── 5. handle_task_done → finance-транзакция из note ─────────────────────────

@pytest.mark.asyncio
async def test_task_done_with_money_note_creates_expense(mock_message):
    msg = mock_message(text="сходила к нотариусу готово")
    task = Task(id="t1", title="прийти к нотариусу", repeat="Нет",
                note="оплата 1250 рублей", user_notion_id="u-1")

    writer = AsyncMock(return_value="fin-1")
    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[task])), \
         patch.object(tasks_mod._repo, "set_status", AsyncMock(return_value=True)), \
         patch.object(tasks_mod, "_remove_task_jobs", MagicMock()), \
         patch.object(tasks_mod, "_update_streak_line", AsyncMock(return_value="")), \
         patch("nexus.handlers.finance.ask_claude", AsyncMock(return_value='{"items":[]}')), \
         patch("nexus.handlers.finance._write_one_time_expense", writer):
        await tasks_mod.handle_task_done(msg, "нотариус", user_notion_id="u-1")

    writer.assert_awaited_once()
    assert writer.await_args.args[1] == 1250.0          # amount
    replies = " ".join(str(c.args[0]) for c in msg.answer.await_args_list)
    assert "Записал расход" in replies
    assert "1" in replies and "250" in replies


@pytest.mark.asyncio
async def test_task_done_note_without_amount_no_expense(mock_message):
    msg = mock_message(text="уточнила детали готово")
    task = Task(id="t2", title="уточнить у риэлтора детали", repeat="Нет",
                note="спросить про документы", user_notion_id="u-1")

    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[task])), \
         patch.object(tasks_mod._repo, "set_status", AsyncMock(return_value=True)) as m_status, \
         patch.object(tasks_mod, "_remove_task_jobs", MagicMock()), \
         patch.object(tasks_mod, "_update_streak_line", AsyncMock(return_value="")), \
         patch("nexus.handlers.finance._write_one_time_expense", AsyncMock()) as writer:
        await tasks_mod.handle_task_done(msg, "риэлтор", user_notion_id="u-1")

    m_status.assert_awaited_once()
    writer.assert_not_awaited()
    replies = " ".join(str(c.args[0]) for c in msg.answer.await_args_list)
    assert "Записал расход" not in replies


@pytest.mark.asyncio
async def test_task_done_no_note_regression(mock_message):
    msg = mock_message(text="покормила кота готово")
    task = Task(id="t3", title="покормить кота", repeat="Нет", user_notion_id="u-1")

    with patch.object(tasks_mod._repo, "active", AsyncMock(return_value=[task])), \
         patch.object(tasks_mod._repo, "set_status", AsyncMock(return_value=True)), \
         patch.object(tasks_mod, "_remove_task_jobs", MagicMock()), \
         patch.object(tasks_mod, "_update_streak_line", AsyncMock(return_value="")), \
         patch("nexus.handlers.finance._write_one_time_expense", AsyncMock()) as writer:
        await tasks_mod.handle_task_done(msg, "кот", user_notion_id="u-1")

    writer.assert_not_awaited()
    msg.answer.assert_awaited_once()


# ── 6. expense_from_task_note сам по себе ────────────────────────────────────

@pytest.mark.asyncio
async def test_expense_from_task_note_uses_haiku_category():
    import nexus.handlers.finance as fin
    writer = AsyncMock(return_value="fin-9")
    haiku = AsyncMock(return_value='{"items":[{"description":"билет","amount":15000,"category":"🚕 Транспорт"}]}')
    with patch.object(fin, "ask_claude", haiku), \
         patch.object(fin, "_write_one_time_expense", writer):
        res = await fin.expense_from_task_note("билет в питер 15000", user_notion_id="u-1")
    assert res == ("билет в питер 15000", 15000.0, "🚕 Транспорт")
    assert writer.await_args.args[2] == "🚕 Транспорт"


@pytest.mark.asyncio
async def test_expense_from_task_note_none_without_amount():
    import nexus.handlers.finance as fin
    with patch.object(fin, "_write_one_time_expense", AsyncMock()) as writer:
        res = await fin.expense_from_task_note("позвонить риэлтору")
    assert res is None
    writer.assert_not_awaited()
