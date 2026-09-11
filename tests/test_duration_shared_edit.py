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
