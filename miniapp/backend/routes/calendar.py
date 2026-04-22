"""miniapp/backend/routes/calendar.py — GET /api/calendar."""
from __future__ import annotations

import calendar as _calendar
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    extract_time,
    prio_from_notion,
    select_name,
    title_text,
    to_local_date,
    today_user_tz,
)

logger = logging.getLogger("miniapp.calendar")

router = APIRouter()


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    start = f"{y}-{m:02d}-01"
    last_day = _calendar.monthrange(y, m)[1]
    end = f"{y}-{m:02d}-{last_day:02d}"
    return start, end


async def _fetch_tasks_in_month(user_notion_id: str, start: str, end: str) -> list[dict]:
    # База задач — Nexus-only; фильтр по "Бот" вызывает 400 от Notion.
    conditions: list[dict] = [
        {"property": "Дедлайн", "date": {"on_or_after": start}},
        {"property": "Дедлайн", "date": {"on_or_before": end}},
    ]
    if user_notion_id:
        conditions.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    return await query_pages(
        config.nexus.db_tasks,
        filters={"and": conditions},
        sorts=[{"property": "Дедлайн", "direction": "ascending"}],
        page_size=500,
    )


@router.get("/calendar")
async def get_calendar(
    tg_id: int = Depends(current_user_id),
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> dict[str, Any]:
    today_date, tz_offset = await today_user_tz(tg_id)
    if not month:
        month = today_date.strftime("%Y-%m")

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    start, end = _month_bounds(month)
    raw = await _fetch_tasks_in_month(user_notion_id, start, end)

    y, m = int(month[:4]), int(month[5:7])
    days_in_month = _calendar.monthrange(y, m)[1]
    days: dict[str, dict] = {
        str(d): {"count": 0, "has_overdue": False, "has_high_prio": False, "tasks": []}
        for d in range(1, days_in_month + 1)
    }

    for p in raw:
        props = p.get("properties", {})
        deadline_raw = (props.get("Дедлайн", {}).get("date") or {}).get("start") or ""
        deadline_local = to_local_date(deadline_raw, tz_offset)
        if not deadline_local or deadline_local.month != m or deadline_local.year != y:
            continue

        status = (props.get("Статус", {}).get("status") or {}).get("name", "")
        if status in ("Archived",):
            continue

        prio = prio_from_notion(select_name(props.get("Приоритет", {})))
        day_key = str(deadline_local.day)
        bucket = days[day_key]
        bucket["tasks"].append({
            "id": p.get("id", ""),
            "title": title_text(props.get("Задача", {})),
            "prio": prio,
            "time": extract_time(deadline_raw, tz_offset),
        })
        bucket["count"] += 1
        if status not in ("Done", "Complete") and deadline_local < today_date:
            bucket["has_overdue"] = True
        if prio == "🔴":
            bucket["has_high_prio"] = True

    return {"month": month, "days": days}
