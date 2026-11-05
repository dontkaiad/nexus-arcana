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
    interval_days INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, task_id)
);
"""


def _init_db() -> None:
    os.makedirs(_DB_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(_CREATE_SQL)
        # Существующие БД создавались без interval_days — добавляем колонку на месте.
        cols = {r[1] for r in con.execute("PRAGMA table_info(task_streaks)")}
        if "interval_days" not in cols:
            con.execute("ALTER TABLE task_streaks ADD COLUMN interval_days INTEGER DEFAULT 0")
        con.commit()
    finally:
        con.close()


_init_db()


def _period_days(repeat_kind: str, interval_days: int = 0) -> int:
    """Шаг между двумя успешными выполнениями.

    «Каждые N дней» (interval_days > 1, из «Времени повтора» HH:MM|every_Nd) → N —
    «Повтор» у такой задачи остаётся «Ежедневно», и без этого шаг считался за 1 день:
    стрик задачи «каждые 2 дня» никогда не рос выше 1.
    «Каждый день» → 1, «Каждую неделю» → 7. Иначе fallback 1
    (трактуем как daily-подобную).
    """
    if interval_days and interval_days > 1:
        return int(interval_days)
    rk = (repeat_kind or "").strip().lower()
    if "недел" in rk:
        return 7
    if "месяц" in rk:
        return 30
    return 1


def _is_daily(repeat_kind: str) -> bool:
    """"Ежедневно" — канонический repeat_kind ежедневных задач (см.
    nexus/handlers/tasks.py). Раньше проверка была на substring "ден", которого
    в слове "ежедневно" нет (е-ж-е-д-н-е-в-н-о — есть "дне", не "ден") —
    reset_broken_streaks никогда не срабатывал для ежедневных стриков."""
    rk = (repeat_kind or "").strip().lower()
    return rk in ("", "каждый день", "ежедневно")


def _date_minus(date_str: str, days: int) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def update_task_streak(
    user_id: int,
    task_id: str,
    task_title: str,
    repeat_kind: str,
    today_local: str,
    interval_days: int = 0,
) -> Optional[dict]:
    """UPSERT стрика по задаче. Возвращает dict с новыми current/best или
    None если это дубль за день."""
    interval_days = max(int(interval_days or 0), 0)
    period = _period_days(repeat_kind, interval_days)
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
                "current_streak, best_streak, last_done_date, interval_days) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, task_id, task_title, repeat_kind,
                 current, best, today_local, interval_days),
            )
            con.commit()
            return {"current": current, "best": best, "is_dup": False}

        cur_curr, cur_best, last_date = row
        if last_date == today_local:
            # Дубль за день — обновляем мета (title/repeat могли поменяться),
            # но счётчики не трогаем.
            con.execute(
                "UPDATE task_streaks SET task_title = ?, repeat_kind = ?, interval_days = ? "
                "WHERE user_id = ? AND task_id = ?",
                (task_title, repeat_kind, interval_days, user_id, task_id),
            )
            con.commit()
            return None

        if last_date == expected_prev:
            new_current = (cur_curr or 0) + 1
        else:
            new_current = 1
        new_best = max(cur_best or 0, new_current)
        con.execute(
            "UPDATE task_streaks SET task_title = ?, repeat_kind = ?, interval_days = ?, "
            "current_streak = ?, best_streak = ?, last_done_date = ? "
            "WHERE user_id = ? AND task_id = ?",
            (task_title, repeat_kind, interval_days, new_current, new_best, today_local,
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
            "SELECT task_id, repeat_kind, current_streak, last_done_date, interval_days "
            "FROM task_streaks WHERE user_id = ? AND current_streak > 0",
            (user_id,),
        ).fetchall()
        reset = 0
        for task_id, repeat_kind, _curr, last_date, ivl in rows:
            ivl = int(ivl or 0)
            if ivl > 1:
                # «Каждые N дней»: цепочка рвётся, если с последнего выполнения прошло больше N дней.
                limit = _date_minus(today_local, ivl)
            elif _is_daily(repeat_kind or ""):
                limit = yesterday
            else:
                continue
            if not last_date:
                continue
            if last_date < limit:
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
