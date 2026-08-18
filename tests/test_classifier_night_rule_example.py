"""Regression: build_system's night-owl rule ("до 05:00 'завтра' = СЕГОДНЯ")
was contradicted by its own few-shot example — 'напомни написать Диме завтра
в 9 вечера' always computed the answer as now+1 day, regardless of the hour.
A concrete worked example structurally identical to the user's real message
("напомни [что-то] завтра в [время]") outweighs an abstract rule stated
elsewhere in the prompt, so at night the model followed the example instead
of the rule.

Live report: at 01:00, "напомни завтра в 14 достать рыбу" resolved to
tomorrow (+1 day) instead of today, exactly matching the contradicting
example's answer shape.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import core.classifier as clf


def _build_at(hour: int, minute: int = 0) -> str:
    fake_now = datetime(2026, 8, 19, hour, minute, tzinfo=timezone(timedelta(hours=3)))

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now.astimezone(tz) if tz else fake_now

    with patch.object(clf, "datetime", FakeDatetime):
        return clf.build_system(3)


def _example_reminder_date(system: str) -> str:
    idx = system.find("написать Диме")
    idx = system.find('"reminder":"', idx)
    return system[idx + len('"reminder":"'):idx + len('"reminder":"') + 10]


def test_example_matches_night_rule_at_night():
    system = _build_at(1)
    assert "НОЧНАЯ ЛОГИКА" in system
    assert _example_reminder_date(system) == "2026-08-19"  # today, not +1 day


def test_example_is_real_tomorrow_during_day():
    system = _build_at(14)
    assert "НОЧНАЯ ЛОГИКА" not in system
    assert _example_reminder_date(system) == "2026-08-20"  # real tomorrow
