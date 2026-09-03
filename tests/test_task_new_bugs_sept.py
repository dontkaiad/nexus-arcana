"""Regression: баги из отчёта Кай (сентябрь). #198 / #199 / #200 / #201.

1. reply «добавь заметку <сумма> торг» на созданную задачу → «Не поняла
   что дополнить»: в task-схеме reply-дополнения (`core/reply_update.py`)
   не было поля note вообще.
2. «приоритет срочно дедлайн <дата>» в режиме подтверждения задачи →
   применялся только дедлайн: refinement-парсер сверял priority строгим
   `in ("Срочно", ...)` и молча ронял вариант в нижнем регистре / с иным
   написанием, плюс промпт не подсказывал возвращать несколько полей.
3. новая задача «<новая задача> напомни завтра в 14 дедлайн <дата>» уходила уточнением в ПРЕДЫДУЩУЮ задачу: `_CLARIFY_RE`
   матчит «напомни» в любом месте строки.

Privacy: синтетические id, generic тексты.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.reply_update as ru
from nexus.handlers import tasks as T


# ── Баг 1 ────────────────────────────────────────────────────────────────────

def test_task_reply_schema_has_note_field():
    system = ru._task_reply_system(3)
    assert '"note"' in system
    assert "заметк" in system.lower()
    assert "note" in ru._TASK_FIELDS


@pytest.mark.asyncio
async def test_task_reply_note_written_to_zametka_prop():
    repo = MagicMock()
    repo.retrieve_page = AsyncMock(return_value=MagicMock(note=""))
    with patch("nexus.repos.pg_tasks_repo.PgTasksRepo") as PgCls, \
         patch("nexus.repos.tasks_repo._repo", repo):
        PgCls.return_value.set_props = AsyncMock()
        applied = await ru.apply_updates(
            "7", "task", None, {"note": "деталь торга X"}, tz_offset=3,
        )
        _, props = PgCls.return_value.set_props.await_args.args
    assert "деталь торга X" in str(props["Заметка"])
    assert "Заметка" in applied


@pytest.mark.asyncio
async def test_task_reply_note_appends_to_existing():
    repo = MagicMock()
    repo.retrieve_page = AsyncMock(return_value=MagicMock(note="старая"))
    with patch("nexus.repos.pg_tasks_repo.PgTasksRepo") as PgCls, \
         patch("nexus.repos.tasks_repo._repo", repo):
        PgCls.return_value.set_props = AsyncMock()
        await ru.apply_updates("7", "task", None, {"note": "новая"}, tz_offset=3)
        _, props = PgCls.return_value.set_props.await_args.args
    body = str(props["Заметка"])
    assert "старая" in body and "новая" in body


# ── Баг 2 ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refinement_applies_priority_and_deadline_together():
    pending = {"title": "продать предмет", "category": "🗂️ Прочее",
               "priority": "Можно потом"}
    uid = 4242
    msg = MagicMock()
    msg.chat = MagicMock(id=uid)
    msg.answer = AsyncMock()

    combined = '{"deadline": "2026-09-13", "priority": "срочно", "not_refinement": false}'
    with patch.object(T, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(T, "ask_claude", AsyncMock(return_value=combined)), \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_show_task_confirm", AsyncMock()), \
         patch.object(T, "_parse_relative_time", lambda *a, **k: None):
        await T._handle_task_refinement(msg, "приоритет срочно дедлайн 13 сентября",
                                        pending, uid)

    assert pending["priority"] == "Срочно"
    assert pending["deadline"] == "2026-09-13"
    pset.assert_called()


# ── Баг 3 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "напомни завтра в 14",
    "дедлайн 13 сентября",
    "приоритет срочно",
    "категория коты",
])
def test_pure_clarifications_recognised(text):
    assert T._is_clarification_not_new_task(text) is True


@pytest.mark.asyncio
async def test_deadline_in_text_skips_when_to_do_question():
    """Со скрина: «подготовить … дедлайн 13 сентября» — дата уже распознана и
    стоит на карточке, но бот всё равно спрашивал «Когда сделать?».
    Теперь при известном дедлайне (и без «напомни») показываем обычную
    карточку подтверждения «Всё верно?», как во flow работ Arcana."""
    msg = MagicMock()
    msg.chat = MagicMock(id=999)
    msg.text = "подготовить предмет к продаже дедлайн 13 сентября"
    msg.answer = AsyncMock(return_value=MagicMock(message_id=5))
    data = {"title": "подготовить предмет к продаже", "deadline": "2026-09-13",
            "category": "🏠 Жильё", "repeat": "Нет"}

    with patch.object(T, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(T, "_do_save_task", AsyncMock()) as save, \
         patch.object(T, "_pending_set", lambda *a, **k: None), \
         patch.object(T, "_has_remind_word", lambda *a, **k: False):
        await T.handle_task_parsed(msg, data,
                                   original_text="подготовить предмет к продаже дедлайн 13 сентября")

    save.assert_not_awaited()
    sent = msg.answer.await_args.args[0]
    assert "Когда сделать" not in sent
    assert "Всё верно" in sent
    assert "2026-09-13" in sent


@pytest.mark.parametrize("text", [
    "раздать вещи по списку напомни завтра в 14 дедлайн 13 сентября",
    "собрать документы для заявки дедлайн завтра",
    "подготовить отчёт за квартал напомни в 18",
])
def test_new_task_with_embedded_keyword_not_clarification(text):
    assert T._is_clarification_not_new_task(text) is False
