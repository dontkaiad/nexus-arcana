"""miniapp/backend/routes/calendar.py — GET /api/calendar."""
from __future__ import annotations

import calendar as _calendar
import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from core.config import config
from core.notion_client import query_pages
from core.user_manager import get_user_notion_id

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    BOT_NEXUS,
    cat_from_notion,
    extract_time,
    prio_from_notion,
    rich_text,
    select_name,
    status_name,
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
    # Ищем задачи, которые могут попасть в календарь месяца:
    #   - Дедлайн ∈ [start, end]
    #   - Напоминание ∈ [start, end]
    #   - Есть «Время повтора» — повторяющаяся, раскрываем на бэке.
    or_branches: list[dict] = [
        {"and": [
            {"property": "Дедлайн", "date": {"on_or_after": start}},
            {"property": "Дедлайн", "date": {"on_or_before": end}},
        ]},
        {"and": [
            {"property": "Напоминание", "date": {"on_or_after": start}},
            {"property": "Напоминание", "date": {"on_or_before": end}},
        ]},
        {"property": "Время повтора", "rich_text": {"is_not_empty": True}},
    ]
    filters: dict = {"and": [{"or": or_branches}]}
    if user_notion_id:
        filters["and"].append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })
    return await query_pages(
        config.nexus.db_tasks,
        filters=filters,
        sorts=[{"property": "Дедлайн", "direction": "ascending"}],
        page_size=500,
    )


_EVERY_RE = re.compile(r"every_(\d+)d")


def _parse_repeat(raw: str) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """'16:00|every_2d' → ('16:00', 2, 'every_2d'). 'every_2d' → (None, 2, 'every_2d')."""
    if not raw:
        return None, None, None
    raw = raw.strip()
    time_val: Optional[str] = None
    interval_str: Optional[str] = raw
    if "|" in raw:
        t, r = raw.split("|", 1)
        time_val = (t or "").strip() or None
        interval_str = (r or "").strip() or None
    elif re.match(r"^\d{1,2}:\d{2}$", raw):
        time_val = raw
        interval_str = None
    m = _EVERY_RE.search(interval_str or "")
    interval = int(m.group(1)) if m else None
    return time_val, interval, interval_str


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
    month_start = date(y, m, 1)
    month_end = date(y, m, days_in_month)

    days: dict[str, dict] = {
        str(d): {"count": 0, "has_overdue": False, "has_high_prio": False, "tasks": []}
        for d in range(1, days_in_month + 1)
    }

    for p in raw:
        props = p.get("properties", {})
        status = status_name(props.get("Статус", {}))
        if status in ("Archived", "Done", "Complete"):
            continue

        title = title_text(props.get("Задача", {}))
        if not title:
            continue
        prio = prio_from_notion(select_name(props.get("Приоритет", {})))
        cat = cat_from_notion(select_name(props.get("Категория", {}))).get("full") or ""
        repeat_sel = select_name(props.get("Повтор", {})) or None
        repeat_time_raw = rich_text(props.get("Время повтора", {})).strip()
        time_val, interval_days, interval_raw = _parse_repeat(repeat_time_raw)
        repeat_label = interval_raw or repeat_sel

        deadline_raw = (props.get("Дедлайн", {}).get("date") or {}).get("start") or ""
        reminder_raw = (props.get("Напоминание", {}).get("date") or {}).get("start") or ""
        deadline_date = to_local_date(deadline_raw, tz_offset)
        reminder_date = to_local_date(reminder_raw, tz_offset)
        if time_val is None:
            time_val = extract_time(deadline_raw, tz_offset) or extract_time(reminder_raw, tz_offset)

        occurrence_days: list[int] = []
        if interval_days and interval_days > 0:
            anchor = reminder_date or deadline_date
            if anchor:
                # Найти первое вхождение ≥ month_start.
                delta = (month_start - anchor).days
                if delta <= 0:
                    first = anchor
                    while first - timedelta(days=interval_days) >= month_start:
                        first -= timedelta(days=interval_days)
                else:
                    k = (delta + interval_days - 1) // interval_days
                    first = anchor + timedelta(days=k * interval_days)
                d = first
                while d <= month_end:
                    if d >= month_start:
                        occurrence_days.append(d.day)
                    d += timedelta(days=interval_days)
        else:
            if deadline_date and deadline_date.year == y and deadline_date.month == m:
                occurrence_days.append(deadline_date.day)
            elif reminder_date and reminder_date.year == y and reminder_date.month == m:
                occurrence_days.append(reminder_date.day)

        if not occurrence_days:
            continue

        for day_num in occurrence_days:
            day_date = date(y, m, day_num)
            bucket = days[str(day_num)]
            bucket["tasks"].append({
                "id": p.get("id", ""),
                "title": title,
                "cat": cat,
                "prio": prio,
                "time": time_val,
                "repeat": repeat_label,
            })
            bucket["count"] += 1
            if day_date < today_date and not interval_days:
                bucket["has_overdue"] = True
            if prio == "🔴":
                bucket["has_high_prio"] = True

    # Сортировка задач внутри дня: сначала по времени (без времени в конец).
    for bucket in days.values():
        bucket["tasks"].sort(key=lambda t: (t.get("time") is None, t.get("time") or ""))

    return {"month": month, "days": days}
