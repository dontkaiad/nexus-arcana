"""nexus/handlers/streaks.py — Streak tracking for daily task completion."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger("nexus.streaks")

REST_DAY_RE = re.compile(
    r"(?:день\s+отдыха|передышка|отдыхаю\s+сегодня)", re.IGNORECASE
)

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
_DB_PATH = os.path.join(_DB_DIR, "nexus_streaks.db")

_CREATE_SQL = """\
CREATE TABLE IF NOT EXISTS streaks (
    user_id INTEGER PRIMARY KEY,
    current_streak INTEGER DEFAULT 0,
    best_streak INTEGER DEFAULT 0,
    last_activity_date TEXT,
    rest_day_date TEXT,
    rest_days_used INTEGER DEFAULT 0,
    streak_start_date TEXT
);
"""


def _init_db() -> None:
    """Create data directory and streaks table on module load."""
    os.makedirs(_DB_DIR, exist_ok=True)
    import sqlite3
    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(_CREATE_SQL)
        con.commit()
    finally:
        con.close()


_init_db()


def _local_today(tz_offset: int = 3) -> str:
    """Return today's date string (YYYY-MM-DD) in the given UTC offset."""
    tz = timezone(timedelta(hours=tz_offset))
    return datetime.now(tz).strftime("%Y-%m-%d")


def _date_minus(date_str: str, days: int) -> str:
    """Subtract *days* from a YYYY-MM-DD string, return YYYY-MM-DD."""
    dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def update_streak(user_id: int, tz_offset: int = 3) -> Optional[dict]:
    """Call after task completion.

    Returns ``{"streak": int, "best": int, "is_new_best": bool}`` when the
    streak counter changed, or ``None`` if the user already checked in today.
    """
    today = _local_today(tz_offset)
    yesterday = _date_minus(today, 1)
    day_before = _date_minus(today, 2)

    async with aiosqlite.connect(_DB_PATH) as db:
        row = await db.execute(
            "SELECT current_streak, best_streak, last_activity_date, "
            "rest_day_date, rest_days_used, streak_start_date "
            "FROM streaks WHERE user_id = ?",
            (user_id,),
        )
        row = await row.fetchone()

        if row is None:
            # first ever activity
            await db.execute(
                "INSERT INTO streaks "
                "(user_id, current_streak, best_streak, last_activity_date, "
                "rest_days_used, streak_start_date) "
                "VALUES (?, 1, 1, ?, 0, ?)",
                (user_id, today, today),
            )
            await db.commit()
            return {"streak": 1, "best": 1, "is_new_best": True}

        current, best, last_date, rest_date, rest_used, start_date = row

        if last_date == today:
            return None  # already counted today

        if last_date == yesterday:
            # consecutive day
            new_streak = current + 1
            new_start = start_date or today
        elif last_date == day_before and rest_date == yesterday:
            # rest day bridged the gap
            new_streak = current + 1
            new_start = start_date or today
        else:
            # streak broken
            new_streak = 1
            new_start = today

        new_best = max(best, new_streak)
        is_new_best = new_best > best

        await db.execute(
            "UPDATE streaks SET current_streak = ?, best_streak = ?, "
            "last_activity_date = ?, streak_start_date = ? "
            "WHERE user_id = ?",
            (new_streak, new_best, today, new_start, user_id),
        )
        await db.commit()

    return {"streak": new_streak, "best": new_best, "is_new_best": is_new_best}


def get_streak(user_id: int) -> dict:
    """Return current streak data (sync, for quick reads)."""
    import sqlite3
    con = sqlite3.connect(_DB_PATH)
    try:
        row = con.execute(
            "SELECT current_streak, best_streak, last_activity_date, "
            "rest_day_date, rest_days_used, streak_start_date "
            "FROM streaks WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        con.close()

    if row is None:
        return {
            "streak": 0,
            "best": 0,
            "last_activity_date": None,
            "rest_day_date": None,
            "rest_days_used": 0,
            "streak_start_date": None,
        }

    return {
        "streak": row[0],
        "best": row[1],
        "last_activity_date": row[2],
        "rest_day_date": row[3],
        "rest_days_used": row[4],
        "streak_start_date": row[5],
    }


def format_streak_msg(streak: int, best: int, is_new_best: bool = False) -> str:
    """Return a praise message based on streak length (Russian)."""
    msg = ""
    if streak < 3:
        msg = ""
    elif streak == 3:
        msg = "\U0001f525 3 \u0434\u043d\u044f \u043f\u043e\u0434\u0440\u044f\u0434! \u0420\u0430\u0437\u0433\u043e\u043d\u044f\u0435\u0448\u044c\u0441\u044f!"
    elif 4 <= streak <= 6:
        msg = "\U0001f525{} \u041e\u0433\u043e\u043d\u044c, \u043d\u0435 \u043e\u0441\u0442\u0430\u043d\u0430\u0432\u043b\u0438\u0432\u0430\u0439\u0441\u044f!".format(streak)
    elif streak == 7:
        msg = "\U0001f525\U0001f525 \u041d\u0435\u0434\u0435\u043b\u044f! \u0422\u044b \u043c\u0430\u0448\u0438\u043d\u0430!"
    elif 8 <= streak <= 13:
        msg = "\U0001f525\U0001f525{} \u0434\u043d\u0435\u0439! \u0421\u0438\u043b\u0430 \u043f\u0440\u0438\u0432\u044b\u0447\u043a\u0438!".format(streak)
    elif streak == 14:
        msg = "\U0001f525\U0001f525\U0001f525 2 \u043d\u0435\u0434\u0435\u043b\u0438! \u042d\u0442\u043e \u0443\u0436\u0435 \u043f\u0440\u0438\u0432\u044b\u0447\u043a\u0430!"
    elif 15 <= streak <= 29:
        msg = "\U0001f525\U0001f525\U0001f525{} \u2014 \u043b\u0435\u0433\u0435\u043d\u0434\u0430!".format(streak)
    elif streak == 30:
        msg = "\u2b50\U0001f525 \u041c\u0415\u0421\u042f\u0426! \u041b\u0435\u0433\u0435\u043d\u0434\u0430\u0440\u043d\u043e!"
    elif streak > 30:
        msg = "\u2b50\U0001f525{} \u2014 \u044d\u043b\u0438\u0442\u043d\u044b\u0439 \u043a\u043b\u0443\u0431!".format(streak)

    if is_new_best and best > 0:
        record = "\U0001f3c6 \u041d\u043e\u0432\u044b\u0439 \u0440\u0435\u043a\u043e\u0440\u0434: {} \u0434\u043d\u0435\u0439!".format(best)
        if msg:
            msg = "{}\n{}".format(msg, record)
        else:
            msg = record

    return msg


async def request_rest_day(user_id: int, tz_offset: int = 3) -> str:
    """Request a rest day. Limit: 1 per 5 days.

    Returns a message string to send to the user.
    """
    today = _local_today(tz_offset)

    async with aiosqlite.connect(_DB_PATH) as db:
        row = await db.execute(
            "SELECT current_streak, rest_day_date, rest_days_used "
            "FROM streaks WHERE user_id = ?",
            (user_id,),
        )
        row = await row.fetchone()

        if row is None:
            return "\u0423 \u0442\u0435\u0431\u044f \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u043e\u0433\u043e \u0441\u0442\u0440\u0438\u043a\u0430. \u041d\u0430\u0447\u043d\u0438 \u0432\u044b\u043f\u043e\u043b\u043d\u044f\u0442\u044c \u0437\u0430\u0434\u0430\u0447\u0438!"  # No active streak

        current, last_rest, rest_used = row

        if current < 1:
            return "\u0421\u0442\u0440\u0438\u043a \u0435\u0449\u0451 \u043d\u0435 \u043d\u0430\u0447\u0430\u043b\u0441\u044f \u2014 \u043f\u0435\u0440\u0435\u0434\u044b\u0448\u043a\u0430 \u043d\u0435 \u043d\u0443\u0436\u043d\u0430."  # Streak not started

        # Check cooldown: 1 rest day per 5 days
        if last_rest is not None:
            last_rest_dt = datetime.strptime(last_rest, "%Y-%m-%d")
            today_dt = datetime.strptime(today, "%Y-%m-%d")
            days_since = (today_dt - last_rest_dt).days
            if days_since < 5:
                remaining = 5 - days_since
                return (
                    "\u23f3 \u041f\u0435\u0440\u0435\u0434\u044b\u0448\u043a\u0443 \u043c\u043e\u0436\u043d\u043e \u0431\u0440\u0430\u0442\u044c \u0440\u0430\u0437 \u0432 5 \u0434\u043d\u0435\u0439. "
                    "\u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0430\u044f \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0447\u0435\u0440\u0435\u0437 {} \u0434\u043d.".format(remaining)
                )

        await db.execute(
            "UPDATE streaks SET rest_day_date = ?, rest_days_used = rest_days_used + 1 "
            "WHERE user_id = ?",
            (today, user_id),
        )
        await db.commit()

    return (
        "\U0001f33f \u041f\u0435\u0440\u0435\u0434\u044b\u0448\u043a\u0430 \u0432\u0437\u044f\u0442\u0430! "
        "\u0421\u0442\u0440\u0438\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f, \u0435\u0441\u043b\u0438 \u0437\u0430\u0432\u0442\u0440\u0430 \u0432\u0435\u0440\u043d\u0451\u0448\u044c\u0441\u044f. \u041e\u0442\u0434\u044b\u0445\u0430\u0439 \U0001f60c"
    )


def get_streak_stats(user_id: int) -> str:
    """Formatted streak stats for /stats display."""
    data = get_streak(user_id)
    streak = data["streak"]
    best = data["best"]
    rest_used = data["rest_days_used"]

    lines = [
        "\U0001f525 \u0421\u0442\u0440\u0438\u043a: {} \u0434\u043d\u0435\u0439 \u043f\u043e\u0434\u0440\u044f\u0434".format(streak),
        "\U0001f3c6 \u041b\u0443\u0447\u0448\u0438\u0439: {} \u0434\u043d\u0435\u0439".format(best),
        "\U0001f33f \u041f\u0435\u0440\u0435\u0434\u044b\u0448\u0435\u043a: {}/1 \u0437\u0430 5 \u0434\u043d\u0435\u0439".format(
            _rest_days_in_window(user_id)
        ),
    ]
    return "\n".join(lines)


def rebuild_streak_from_dates(user_id: int, done_dates: list[str], tz_offset: int = 3) -> dict:
    """Пересчитать стрик из списка дат выполненных задач и записать в SQLite.

    done_dates — список строк YYYY-MM-DD (могут повторяться, порядок неважен).
    Возвращает {"streak": int, "best": int}.
    """
    import sqlite3

    today = _local_today(tz_offset)
    unique_dates = sorted({d[:10] for d in done_dates if d}, reverse=True)

    # Считаем текущий стрик (от сегодня назад)
    streak = 0
    check = datetime.strptime(today, "%Y-%m-%d").date()
    from datetime import date as _date
    date_set = {datetime.strptime(d, "%Y-%m-%d").date() for d in unique_dates}

    for _ in range(len(unique_dates) + 1):
        if check in date_set:
            streak += 1
            check -= timedelta(days=1)
        else:
            break

    # Лучший стрик — полный проход по всем датам
    best = 0
    cur = 0
    prev = None
    for d_str in reversed(unique_dates):
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        if prev is None or (d - prev).days == 1:
            cur += 1
        else:
            best = max(best, cur)
            cur = 1
        prev = d
    best = max(best, cur, streak)

    streak_start = None
    if streak > 0:
        streak_start_date = datetime.strptime(today, "%Y-%m-%d").date() - timedelta(days=streak - 1)
        streak_start = streak_start_date.strftime("%Y-%m-%d")

    last_activity = unique_dates[0] if unique_dates else None

    con = sqlite3.connect(_DB_PATH)
    try:
        con.execute(
            "INSERT OR REPLACE INTO streaks "
            "(user_id, current_streak, best_streak, last_activity_date, "
            "rest_days_used, streak_start_date) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT rest_days_used FROM streaks WHERE user_id=?), 0), ?)",
            (user_id, streak, best, last_activity, user_id, streak_start),
        )
        con.commit()
    finally:
        con.close()

    return {"streak": streak, "best": best}


def _rest_days_in_window(user_id: int) -> int:
    """Return how many rest days were used in the last 5 days (0 or 1)."""
    data = get_streak(user_id)
    rest_date = data.get("rest_day_date")
    if rest_date is None:
        return 0
    today_str = _local_today()
    last_rest_dt = datetime.strptime(rest_date, "%Y-%m-%d")
    today_dt = datetime.strptime(today_str, "%Y-%m-%d")
    if (today_dt - last_rest_dt).days < 5:
        return 1
    return 0
