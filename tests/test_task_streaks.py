"""tests/test_task_streaks.py — per-task стрики."""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Изолированная SQLite-копия для каждого теста."""
    db_path = tmp_path / "nexus_streaks.db"
    # Перезагружаем модуль с подменённым путём.
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    sys.modules.pop("core.task_streaks", None)
    import core.task_streaks as ts
    monkeypatch.setattr(ts, "_DB_PATH", str(db_path))
    # Создаём таблицу в новом файле.
    con = sqlite3.connect(str(db_path))
    con.execute(ts._CREATE_SQL)
    con.commit()
    con.close()
    return ts


def test_new_task_creates_streak_one(fresh_db):
    ts = fresh_db
    r = ts.update_task_streak(42, "task-A", "Зарядка", "Каждый день", "2026-05-03")
    assert r == {"current": 1, "best": 1, "is_dup": False}
    rows = ts.get_user_task_streaks(42)
    assert len(rows) == 1
    assert rows[0]["title"] == "Зарядка"
    assert rows[0]["current"] == 1
    assert rows[0]["best"] == 1


def test_consecutive_days_extend_streak(fresh_db):
    ts = fresh_db
    ts.update_task_streak(42, "t", "Зарядка", "Каждый день", "2026-05-01")
    ts.update_task_streak(42, "t", "Зарядка", "Каждый день", "2026-05-02")
    r = ts.update_task_streak(42, "t", "Зарядка", "Каждый день", "2026-05-03")
    assert r == {"current": 3, "best": 3, "is_dup": False}


def test_duplicate_same_day_is_noop(fresh_db):
    ts = fresh_db
    ts.update_task_streak(42, "t", "Зарядка", "Каждый день", "2026-05-03")
    r = ts.update_task_streak(42, "t", "Зарядка", "Каждый день", "2026-05-03")
    assert r is None
    rows = ts.get_user_task_streaks(42)
    assert rows[0]["current"] == 1
    assert rows[0]["best"] == 1


def test_gap_resets_current_keeps_best(fresh_db):
    ts = fresh_db
    # 3 дня подряд → best=3
    for d in ("2026-05-01", "2026-05-02", "2026-05-03"):
        ts.update_task_streak(42, "t", "Зарядка", "Каждый день", d)
    # Пропуск 4 и 5 мая. Возвращаемся 6 мая.
    r = ts.update_task_streak(42, "t", "Зарядка", "Каждый день", "2026-05-06")
    assert r == {"current": 1, "best": 3, "is_dup": False}


def test_reset_broken_streaks_only_for_daily(fresh_db):
    ts = fresh_db
    # daily — last_done = позавчера → должен сброситься
    ts.update_task_streak(42, "t-daily", "Daily", "Каждый день", "2026-05-01")
    # weekly — last_done = позавчера → НЕ сбрасываем (не каждый день)
    ts.update_task_streak(42, "t-weekly", "Weekly", "Каждую неделю", "2026-05-01")
    n = ts.reset_broken_streaks(42, "2026-05-03")
    assert n == 1
    rows = {r["task_id"]: r for r in ts.get_user_task_streaks(42)}
    assert rows["t-daily"]["current"] == 0
    assert rows["t-daily"]["best"] == 1   # best не тронут
    assert rows["t-weekly"]["current"] == 1


def test_weekly_extension_uses_7_day_period(fresh_db):
    ts = fresh_db
    ts.update_task_streak(42, "w", "Уборка", "Каждую неделю", "2026-04-26")
    r = ts.update_task_streak(42, "w", "Уборка", "Каждую неделю", "2026-05-03")
    assert r == {"current": 2, "best": 2, "is_dup": False}


def test_get_user_task_streaks_sorted_by_current_then_best(fresh_db):
    ts = fresh_db
    ts.update_task_streak(42, "low", "low", "Каждый день", "2026-05-03")
    ts.update_task_streak(42, "mid", "mid", "Каждый день", "2026-05-02")
    ts.update_task_streak(42, "mid", "mid", "Каждый день", "2026-05-03")
    ts.update_task_streak(42, "hi", "hi", "Каждый день", "2026-05-01")
    ts.update_task_streak(42, "hi", "hi", "Каждый день", "2026-05-02")
    ts.update_task_streak(42, "hi", "hi", "Каждый день", "2026-05-03")
    rows = ts.get_user_task_streaks(42)
    assert [r["task_id"] for r in rows] == ["hi", "mid", "low"]
    assert [r["current"] for r in rows] == [3, 2, 1]
