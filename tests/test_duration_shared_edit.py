"""tests/test_duration_shared_edit.py — NL edit-record routing for the new
'duration' (#241) and 'shared' (#242) fields.

Mirrors tests/test_edit_record_native_repro.py's pattern: verify the fast
regex pre-filter (_EDIT_RE / _SHARE_RE) actually reaches _parse_edit_record
(otherwise the new fields in _EDIT_PARSE_SYSTEM are dead prompt text — see
_apply_edit's "duration"/"shared" branches in nexus/handlers/tasks.py for
the persistence side).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import core.classifier as clf


@pytest.mark.parametrize("text", [
    "поставь длительность 2 часа для встречи с Мишаней",
    "измени длительность на 30 минут",
    "поменяй продолжительность встречи на час",
])
def test_edit_re_matches_duration(text):
    assert clf._EDIT_RE.search(text)


@pytest.mark.parametrize("text", [
    "расшарь задачу сходить в кино",
    "поделись задачей купить корм",
    "покажи друзьям задачу сходить в кино",
    "скрой задачу купить корм",
    "убери из расшаренного задачу купить корм",
])
def test_share_re_matches(text):
    assert clf._SHARE_RE.search(text)


@pytest.mark.parametrize("negative", [
    "купить молоко",
    "напомни завтра позвонить маме",
    "продолжительность фильма 2 часа",  # no verb — just narration, not an edit
])
def test_share_and_duration_re_do_not_overtrigger(negative):
    assert not clf._SHARE_RE.search(negative)


@pytest.mark.asyncio
async def test_duration_edit_routes_to_edits_list_parser():
    text = "поставь длительность 2 часа для встречи с Мишаней"
    fake_response = json.dumps({
        "type": "edit_record", "record_type": "task", "record_hint": "встреча с Мишаней",
        "edits": [{"field": "duration", "new_value": "2 часа"}],
    })
    with patch.object(clf, "ask_claude", AsyncMock(return_value=fake_response)):
        items = await clf.classify(text, tz_offset=3)
    assert len(items) == 1
    assert items[0]["type"] == "edit_record"
    assert items[0]["edits"][0]["field"] == "duration"


@pytest.mark.asyncio
async def test_shared_edit_routes_to_edits_list_parser():
    text = "расшарь задачу сходить в кино"
    fake_response = json.dumps({
        "type": "edit_record", "record_type": "task", "record_hint": "сходить в кино",
        "edits": [{"field": "shared", "new_value": "true"}],
    })
    with patch.object(clf, "ask_claude", AsyncMock(return_value=fake_response)):
        items = await clf.classify(text, tz_offset=3)
    assert len(items) == 1
    assert items[0]["edits"][0] == {"field": "shared", "new_value": "true"}


@pytest.mark.asyncio
async def test_note_edit_routes_to_edits_list_parser():
    """Регрессия: 'добавь заметку' раньше не было в field enum/field_map —
    Haiku возвращал field='note' (единственное английское слово, похожее на
    'заметка'), который затем не распознавался нигде дальше по цепочке."""
    text = "добавь к задаче сдать биометрию заметку: адрес такой-то, с собой паспорт"
    fake_response = json.dumps({
        "type": "edit_record", "record_type": "task", "record_hint": "сдать биометрию",
        "edits": [{"field": "note", "new_value": "адрес такой-то, с собой паспорт"}],
    })
    with patch.object(clf, "ask_claude", AsyncMock(return_value=fake_response)):
        items = await clf.classify(text, tz_offset=3)
    assert len(items) == 1
    assert items[0]["edits"][0] == {"field": "note", "new_value": "адрес такой-то, с собой паспорт"}


@pytest.mark.asyncio
async def test_apply_edit_appends_note_instead_of_unknown_field_error():
    """Регрессия: '_apply_edit' не знал field='note' → отвечал
    '⚠️ Не знаю поле «note»' пользователю (сырой внутренний ключ, да ещё
    без обработки). Теперь пишет в PG и дописывает к существующей заметке."""
    from unittest.mock import AsyncMock, MagicMock
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task

    existing_task = Task(id="t1", title="сдать биометрию", note="старая заметка")
    calls = []

    async def capture(pid, props):
        calls.append((pid, props))

    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=1)

    from nexus.repos import pg_tasks_repo
    with patch.object(tasks._repo, "retrieve_page", AsyncMock(return_value=existing_task)), \
         patch.object(tasks._repo, "set_props", AsyncMock(side_effect=capture)), \
         patch.object(pg_tasks_repo, "_ensure_lookups", MagicMock()):
        await tasks._apply_edit(msg, "task", "t1", "сдать биометрию", "note", "новый адрес")

    assert len(calls) == 1
    pid, props = calls[0]
    assert pid == "t1"
    assert props["Заметка"]["rich_text"][0]["text"]["content"] == "старая заметка\nновый адрес"
    reply = msg.answer.call_args[0][0]
    assert "Не знаю поле" not in reply
