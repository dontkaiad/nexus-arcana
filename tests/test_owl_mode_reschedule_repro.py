"""Repro for the reported bug: at 00:10 (night, before 05:00), replying
'завтра в 9' to the reschedule prompt scheduled 2026-08-29 instead of
2026-08-28 (today). Verifies _haiku_parse_reminder_dt now applies the
same night-owl correction as core/classifier.py.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import nexus.handlers.tasks as tasks


def _fake_now_patch(hour: int, minute: int = 0, day: int = 28, month: int = 8, year: int = 2026):
    fake_now = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=3)))

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now.astimezone(tz) if tz else fake_now

    return patch.object(tasks, "datetime", FakeDatetime)


@pytest.mark.asyncio
async def test_reschedule_night_owl_corrects_wrong_tomorrow():
    with _fake_now_patch(hour=0, minute=10), \
         patch.object(tasks, "ask_claude", AsyncMock(return_value='{"reminder_time": "2026-08-29T09:00"}')):
        result = await tasks._haiku_parse_reminder_dt("завтра в 9", 3)
    assert result == "2026-08-28T09:00"


@pytest.mark.asyncio
async def test_reschedule_day_leaves_real_tomorrow_alone():
    with _fake_now_patch(hour=14, minute=0), \
         patch.object(tasks, "ask_claude", AsyncMock(return_value='{"reminder_time": "2026-08-29T09:00"}')):
        result = await tasks._haiku_parse_reminder_dt("завтра в 9", 3)
    assert result == "2026-08-29T09:00"
