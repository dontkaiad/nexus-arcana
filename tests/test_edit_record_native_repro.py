"""Repro for reported bug: native (non-reply) 'перенеси X на DATE и
напоминалку в TIME' — a compound single-record edit — fell through to the
general classify() prompt (no 'edits' list support there), which sometimes
answers with TWO separate ```json blocks instead of one array. The naive
single json.loads then raised 'Extra data', producing a raw JSON dump shown
to the user instead of applying the edit.

Reply-based '"перенеси на X"' already worked via core/reply_update.py — this
covers the native path reaching parity.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import core.classifier as clf


def test_edit_re_matches_native_perenesi_compound():
    text = "перенеси нотариуса на 28.08 10:00 и напоминалку в 9:30"
    assert clf._EDIT_RE.search(text)


@pytest.mark.parametrize("negative", [
    "перенеси в заметки",
    "купить молоко",
    "напомни завтра позвонить маме",
])
def test_edit_re_does_not_overtrigger(negative):
    assert not clf._EDIT_RE.search(negative)


@pytest.mark.asyncio
async def test_native_perenesi_routes_to_edits_list_parser():
    """Routes through the dedicated _parse_edit_record (edits-list schema),
    not the general classify() prompt that lacks it."""
    text = "перенеси нотариуса на 28.08 10:00 и напоминалку в 9:30"
    fake_response = json.dumps({
        "type": "edit_record", "record_type": "task", "record_hint": "нотариус",
        "edits": [
            {"field": "deadline", "new_value": "2026-08-28T10:00"},
            {"field": "reminder", "new_value": "2026-08-28T09:30"},
        ],
    })
    with patch.object(clf, "ask_claude", AsyncMock(return_value=fake_response)):
        items = await clf.classify(text, tz_offset=3)
    assert len(items) == 1
    assert items[0]["type"] == "edit_record"
    assert len(items[0]["edits"]) == 2


@pytest.mark.asyncio
async def test_classify_recovers_from_multi_block_response():
    """Safety net: if the general classify() prompt ever gets used for a
    compound edit again and Claude answers with two separate ```json blocks
    (the exact failure from the incident log — 'Extra data: line 2 column 1'),
    classify() should recover both items instead of returning parse_error."""
    raw = (
        '```json\n'
        '{"type":"edit_record","record_type":"task","record_hint":"нотариус",'
        '"field":"deadline","new_value":"2026-08-28T10:00"}\n'
        '```\n\n'
        '```json\n'
        '{"type":"edit_record","record_type":"task","record_hint":"нотариус",'
        '"field":"reminder","new_value":"2026-08-28T09:30"}\n'
        '```'
    )
    with patch.object(clf, "ask_claude", AsyncMock(return_value=raw)):
        items = await clf.classify("some text that skips all pre-filters xyz123", tz_offset=3)
    assert len(items) == 2
    assert all(i["type"] == "edit_record" for i in items)
    assert {i["field"] for i in items} == {"deadline", "reminder"}
