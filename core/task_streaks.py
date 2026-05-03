"""core/task_streaks.py — стрики per repeating task.

В отличие от user-level стрика (`nexus.handlers.streaks`), здесь мы трекаем
каждую повторяющуюся задачу отдельно: «Зарядка», «Пить воду», и т.п.

Логика продления (`update_task_streak`):
- today == last_done_date → дубль за день, no-op.
- today == last_done_date + period → current += 1, best = max(best, current).
- иначе → current = 1.

`reset_broken_streaks`: lazy-cleanup — если daily-задача не закрывалась
вчера/сегодня, ставим current=0 (best не трогаем). Для weekly/прочего
не сбрасываем — там сложнее правильно определить «прерывание».
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("core.task_streaks")

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_DB_PATH = os.path.join(_DB_DIR, "nexus_streaks.db")

_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS task_streaks (
    user_id INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    task_title TEXT,
    repeat_kind TEXT,
    current_streak INTEGER DEFAULT 0,
    best_streak INTEGER DEFAULT 0,
    last_done_date TEXT,
    PRIMARY KEY (user_id, task_id)
);
"""


def _init_db() -> None:
    os.makedirs(_DB_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(_CREATE_SQL)
        con.commit()
    finally:
        con.close()


_init_db()


def _period_days(repeat_kind: str) -> int:
    """Шаг между двумя успешными выполнениями.

    «Каждый день» → 1, «Каждую неделю» → 7. Иначе fallback 1
    (трактуем как daily-подобную).
    """
    rk = (repeat_kind or "").strip().lower()
    if "недел" in rk:
        return 7
    if "месяц" in rk:
        return 30
    return 1


def _is_daily(repeat_kind: str) -> bool:
    rk = (repeat_kind or "").strip().lower()
    return "ден" in rk or rk == "" or rk == "каждый день"


def _date_minus(date_str: str, days: int) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def update_task_streak(
    user_id: int,
    task_id: str,
    task_title: str,
    repeat_kind: str,
    today_local: str,
) -> Optional[dict]:
    """UPSERT стрика по задаче. Возвращает dict с новыми current/best или
    None если это дубль за день."""
    period = _period_days(repeat_kind)
    expected_prev = _date_minus(today_local, period)

    con = sqlite3.connect(_DB_PATH)
    try:
        row = con.execute(
            "SELECT current_streak, best_streak, last_done_date "
            "FROM task_streaks WHERE user_id = ? AND task_id = ?",
            (user_id, task_id),
        ).fetchone()

        if row is None:
            current, best = 1, 1
            con.execute(
                "INSERT INTO task_streaks "
                "(user_id, task_id, task_title, repeat_kind, "
                "current_streak, best_streak, last_done_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, task_id, task_title, repeat_kind,
                 current, best, today_local),
            )
            con.commit()
            return {"current": current, "best": best, "is_dup": False}

        cur_curr, cur_best, last_date = row
        if last_date == today_local:
            # Дубль за день — обновляем мета (title/repeat могли поменяться),
            # но счётчики не трогаем.
            con.execute(
                "UPDATE task_streaks SET task_title = ?, repeat_kind = ? "
                "WHERE user_id = ? AND task_id = ?",
                (task_title, repeat_kind, user_id, task_id),
            )
            con.commit()
            return None

        if last_date == expected_prev:
            new_current = (cur_curr or 0) + 1
        else:
            new_current = 1
        new_best = max(cur_best or 0, new_current)
        con.execute(
            "UPDATE task_streaks SET task_title = ?, repeat_kind = ?, "
            "current_streak = ?, best_streak = ?, last_done_date = ? "
            "WHERE user_id = ? AND task_id = ?",
            (task_title, repeat_kind, new_current, new_best, today_local,
             user_id, task_id),
        )
        con.commit()
        return {"current": new_current, "best": new_best, "is_dup": False}
    finally:
        con.close()


def reset_broken_streaks(user_id: int, today_local: str) -> int:
    """Lazy cleanup: для daily-задач, если last_done < вчера → current=0.

    Возвращает количество сброшенных строк.
    """
    yesterday = _date_minus(today_local, 1)
    con = sqlite3.connect(_DB_PATH)
    try:
        rows = con.execute(
            "SELECT task_id, repeat_kind, current_streak, last_done_date "
            "FROM task_streaks WHERE user_id = ? AND current_streak > 0",
            (user_id,),
        ).fetchall()
        reset = 0
        for task_id, repeat_kind, _curr, last_date in rows:
            if not _is_daily(repeat_kind or ""):
                continue
            if not last_date:
                continue
            if last_date < yesterday:
                con.execute(
                    "UPDATE task_streaks SET current_streak = 0 "
                    "WHERE user_id = ? AND task_id = ?",
                    (user_id, task_id),
                )
                reset += 1
        if reset:
            con.commit()
        return reset
    finally:
        con.close()


def get_user_task_streaks(user_id: int) -> list[dict]:
    """Все строки юзера. Сортировка: current desc, then best desc."""
    con = sqlite3.connect(_DB_PATH)
    try:
        rows = con.execute(
            "SELECT task_id, task_title, repeat_kind, current_streak, "
            "best_streak, last_done_date "
            "FROM task_streaks WHERE user_id = ? "
            "ORDER BY current_streak DESC, best_streak DESC",
            (user_id,),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "task_id": r[0],
            "title": r[1] or "",
            "repeat": r[2] or "",
            "current": r[3] or 0,
            "best": r[4] or 0,
            "last_done_date": r[5],
        }
        for r in rows
    ]
