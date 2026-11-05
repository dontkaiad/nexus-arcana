"""tests/test_task_streaks_interval.py — стрик задачи «каждые N дней» (repeat_time HH:MM|every_Nd).

«Повтор» такой задачи остаётся «Ежедневно», и шаг цепочки считался за 1 день: при выполнении вовремя каждые
2 дня стрик всегда был 1. Теперь шаг = N (interval_days), сброс — если прошло больше N дней.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta

import pytest


@pytest.fixture
def ts(tmp_path, monkeypatch):
    sys.modules.pop("core.task_streaks", None)
    import core.task_streaks as mod
    monkeypatch.setattr(mod, "_DB_PATH", str(tmp_path / "s.db"))
    mod._init_db()
    return mod


def _day(n: int) -> str:
    return (date(2026, 11, 1) + timedelta(days=n)).isoformat()


def test_every_2_days_on_time_grows_streak(ts):
    for i, d in enumerate(range(0, 10, 2), start=1):
        r = ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(d), interval_days=2)
        assert r["current"] == i
    assert ts.get_user_task_streaks(1)[0]["best"] == 5


def test_every_2_days_not_reset_between_on_time_completions(ts):
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0), interval_days=2)
    assert ts.reset_broken_streaks(1, _day(1)) == 0
    assert ts.reset_broken_streaks(1, _day(2)) == 0          # срок наступил, но ещё не просрочен
    assert ts.get_user_task_streaks(1)[0]["current"] == 1


def test_every_2_days_reset_after_missed_cycle(ts):
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0), interval_days=2)
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(2), interval_days=2)
    assert ts.reset_broken_streaks(1, _day(5)) == 1           # пропущено выполнение дня 4
    assert ts.get_user_task_streaks(1)[0]["current"] == 0


def test_every_2_days_late_completion_restarts_at_one(ts):
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0), interval_days=2)
    r = ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(3), interval_days=2)
    assert r["current"] == 1


def test_plain_daily_unchanged(ts):
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0))
    assert ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(1))["current"] == 2
    assert ts.reset_broken_streaks(1, _day(2)) == 0
    assert ts.reset_broken_streaks(1, _day(3)) == 1


def test_interval_1_is_plain_daily(ts):
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0), interval_days=1)
    assert ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(1), interval_days=1)["current"] == 2


def test_old_db_without_column_is_migrated_in_place(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE task_streaks (user_id INTEGER NOT NULL, task_id TEXT NOT NULL, task_title TEXT, "
                "repeat_kind TEXT, current_streak INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0, "
                "last_done_date TEXT, PRIMARY KEY (user_id, task_id))")
    con.execute("INSERT INTO task_streaks VALUES (1, 't', 'Задача', 'Ежедневно', 2, 2, '2026-11-01')")
    con.commit(); con.close()
    sys.modules.pop("core.task_streaks", None)
    import core.task_streaks as mod
    monkeypatch.setattr(mod, "_DB_PATH", str(path))
    mod._init_db()
    mod._init_db()                                            # повторный запуск не падает
    row = sqlite3.connect(path).execute("SELECT current_streak, interval_days FROM task_streaks").fetchone()
    assert row == (2, 0)                                      # данные сохранены, интервал 0 → поведение как раньше
    assert mod.update_task_streak(1, "t", "Задача", "Ежедневно", "2026-11-02")["current"] == 3


def test_interval_is_saved_and_survives_duplicate_day(ts):
    ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0), interval_days=2)
    assert ts.update_task_streak(1, "t", "Задача", "Ежедневно", _day(0), interval_days=2) is None
    row = sqlite3.connect(ts._DB_PATH).execute("SELECT interval_days FROM task_streaks").fetchone()
    assert row == (2,)


def test_callers_pass_interval_from_repeat_time():
    from core.recurrence import parse_repeat_time
    assert parse_repeat_time("16:00|every_2d")[1] == 2
    assert parse_repeat_time("16:00")[1] == 0
    import inspect
    import miniapp.backend.routes.writes as w
    import nexus.handlers.tasks as t
    assert "interval_days=parse_repeat_time(repeat_time)[1]" in inspect.getsource(w.task_done)
    assert "interval_days=_parse_repeat_time(" in inspect.getsource(t._handle_recurring_deadline_done)
