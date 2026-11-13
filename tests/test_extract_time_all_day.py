"""tests/test_extract_time_all_day.py — Mini App не показывает «03:00» у дедлайнов без времени.

Дедлайн «на весь день» хранится в PG как полночь UTC (`_parse_iso` дополняет
чистую дату) + флаг deadline_all_day=True, а repo отдаёт его полным ISO
('2026-11-27T00:00:00+00:00'). extract_time считал это временем и в МСК
показывал 03:00, игнорируя флаг.

Без БД/LLM. Privacy: нейтральные названия.
"""
from __future__ import annotations

from datetime import date

import pytest

from miniapp.backend._helpers import extract_time
from nexus.repos.pg_tasks_repo import Task

ALL_DAY_ISO = "2026-11-27T00:00:00+00:00"   # так repo отдаёт дедлайн без времени
TIMED_ISO = "2026-11-27T12:00:00+00:00"     # 15:00 МСК


def test_extract_time_all_day_returns_none():
    assert extract_time(ALL_DAY_ISO, 3, all_day=True) is None


def test_extract_time_reproduces_the_03_00_without_flag():
    """Документ причины: без флага полночь UTC в МСК = 03:00."""
    assert extract_time(ALL_DAY_ISO, 3) == "03:00"


def test_extract_time_timed_deadline_unchanged():
    assert extract_time(TIMED_ISO, 3) == "15:00"
    assert extract_time(TIMED_ISO, 3, all_day=False) == "15:00"
    assert extract_time(TIMED_ISO, 3, all_day=True) is None  # флаг сильнее


@pytest.mark.parametrize("raw", ["", "2026-11-27"])
def test_extract_time_pure_date_or_empty_is_none(raw):
    assert extract_time(raw, 3) is None
    assert extract_time(raw, 3, all_day=True) is None


def test_tasks_list_all_day_deadline_has_no_time():
    from miniapp.backend.routes.tasks import _serialize_pg_task

    t = Task(id="1", title="купить корм", deadline=ALL_DAY_ISO, deadline_all_day=True)
    out = _serialize_pg_task(t, date(2026, 11, 13), 3)
    assert out["deadline"] == "2026-11-27"
    assert out["deadline_time"] is None


def test_tasks_list_timed_deadline_keeps_time():
    from miniapp.backend.routes.tasks import _serialize_pg_task

    t = Task(id="2", title="созвон", deadline=TIMED_ISO, deadline_all_day=False)
    out = _serialize_pg_task(t, date(2026, 11, 13), 3)
    assert out["deadline"] == "2026-11-27"
    assert out["deadline_time"] == "15:00"


def test_all_day_deadline_does_not_hide_reminder_time():
    """Время напоминания у задачи с дедлайном на весь день по-прежнему отдаётся."""
    from miniapp.backend.routes.tasks import _serialize_pg_task

    t = Task(id="3", title="отчёт", deadline=ALL_DAY_ISO, deadline_all_day=True,
             reminder="2026-11-20T07:00:00+00:00")
    out = _serialize_pg_task(t, date(2026, 11, 13), 3)
    assert out["deadline_time"] is None
    assert out["reminder_time"] == "10:00"


def test_today_summary_carries_the_all_day_flag_and_time_is_none():
    from miniapp.backend.routes.today import _task_summary

    t = Task(id="4", title="купить корм", deadline=ALL_DAY_ISO, deadline_all_day=True)
    s = _task_summary(t, 3)
    assert s["deadline_all_day"] is True
    assert extract_time(s["deadline_raw"], 3, s["deadline_all_day"]) is None

    timed = _task_summary(Task(id="5", title="созвон", deadline=TIMED_ISO), 3)
    assert timed["deadline_all_day"] is False
    assert extract_time(timed["deadline_raw"], 3, timed["deadline_all_day"]) == "15:00"
