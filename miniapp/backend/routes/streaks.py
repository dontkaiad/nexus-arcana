"""miniapp/backend/routes/streaks.py — GET /api/streaks, /api/streaks/week."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import today_user_tz

logger = logging.getLogger("miniapp.streaks")

router = APIRouter()


# ── /api/streaks ────────────────────────────────────────────────────────────

async def _resolve_last_task_title(task_id: str) -> str:
    """#55: получить заголовок Notion-страницы по task_id.
    Возвращает пустую строку при ошибке — UI просто не показывает строку."""
    if not task_id:
        return ""
    try:
        from core.notion_client import get_page
        from miniapp.backend._helpers import title_text
        page = await get_page(task_id)
        if not page:
            return ""
        return title_text(page.get("properties", {}).get("Задача", {})) or ""
    except Exception as e:
        logger.warning("resolve_last_task_title failed for %s: %s", task_id[:8], e)
        return ""


@router.get("/streaks")
async def get_streaks(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    """Общий стрик пользователя + список per-task стриков повторяющихся задач."""
    from nexus.handlers.streaks import get_streak, is_rest_day_available
    from core.task_streaks import get_user_task_streaks, reset_broken_streaks

    today_date, _tz = await today_user_tz(tg_id)
    try:
        reset_broken_streaks(tg_id, today_date.isoformat())
    except Exception as e:
        logger.warning("reset_broken_streaks failed: %s", e)

    data = get_streak(tg_id)
    rows = get_user_task_streaks(tg_id)
    per_task = [r for r in rows if (r["current"] or 0) > 0 or (r["best"] or 0) > 0]

    # #55: «✓ <название> · <время>» в стрик-шите. Резолвим title по task_id
    # только если последняя засчитанная активность — сегодня (иначе нечего
    # показывать вчерашнее в today-bottom-sheet).
    last_task_id = data.get("last_task_id")
    last_task_at = data.get("last_task_at")
    last_activity = data.get("last_activity_date")
    last_task_title = ""
    if last_task_id and last_activity == today_date.isoformat():
        last_task_title = await _resolve_last_task_title(last_task_id)

    return {
        "current": data.get("streak", 0),
        "best": data.get("best", 0),
        "last_activity_date": last_activity,
        "rest_day_available": is_rest_day_available(tg_id),
        "per_task": per_task,
        "last_task": {
            "id": last_task_id,
            "title": last_task_title,
            "at": last_task_at,
        } if last_task_title else None,
    }


# ── /api/streaks/week ───────────────────────────────────────────────────────

def _has_activity_on(user_id: int, day: date) -> bool:
    """Proxy: берём done_dates из streak-схемы streak_start_date..last_activity_date.

    Для wave6 — считаем активными все дни между streak_start_date и
    last_activity_date включительно. Это неточная, но разумная эвристика.
    """
    from nexus.handlers.streaks import _DB_PATH
    try:
        con = sqlite3.connect(_DB_PATH)
        try:
            row = con.execute(
                "SELECT streak_start_date, last_activity_date FROM streaks WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            con.close()
    except Exception as e:
        logger.warning("streak db read failed: %s", e)
        return False

    if not row:
        return False
    start_s, last_s = row
    if not last_s:
        return False
    try:
        last_d = date.fromisoformat(last_s)
    except ValueError:
        return False
    start_d = None
    if start_s:
        try:
            start_d = date.fromisoformat(start_s)
        except ValueError:
            pass
    if start_d is None:
        return day == last_d
    return start_d <= day <= last_d


_WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


@router.get("/streaks/week")
async def get_streak_week(tg_id: int = Depends(current_user_id)) -> dict[str, Any]:
    """Последние 7 дней — активность (Пн → Вс, заканчивая сегодня)."""
    today_date, _tz = await today_user_tz(tg_id)
    days: list[dict] = []
    for i in range(6, -1, -1):
        d = today_date - timedelta(days=i)
        days.append({
            "date": d.isoformat(),
            "weekday": _WEEKDAYS_RU[d.weekday()],
            "has_activity": _has_activity_on(tg_id, d),
            "is_today": d == today_date,
        })
    return {"days": days}
