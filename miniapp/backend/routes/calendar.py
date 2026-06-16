"""miniapp/backend/routes/calendar.py — GET /api/calendar (PG-native, Notion-free)."""
from __future__ import annotations

import calendar as _calendar
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Union

from core.ru_calendar import get_month_info as _get_ru_month_info

from fastapi import APIRouter, Depends, Query

from core.user_manager import get_user_notion_id
from nexus.repos.pg_tasks_repo import PgTasksRepo, Task as PgTask

from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
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
_tasks_repo = PgTasksRepo()


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    start = f"{y}-{m:02d}-01"
    last_day = _calendar.monthrange(y, m)[1]
    end = f"{y}-{m:02d}-{last_day:02d}"
    return start, end


async def _fetch_tasks_in_month(user_notion_id: str) -> list[PgTask]:
    """PG-native: все активные задачи юзера.

    Проекцию occurrences в нужный месяц делаем в Python ниже (повторяющиеся
    могут иметь anchor в другом месяце). Прежняя 3-запросная пляска
    (deadline / reminder / recurring + dedup) была обходом лимита вложенности
    фильтров Notion — в PG не нужна, `.active()` отдаёт всё разом.
    """
    return await _tasks_repo.active(user_notion_id)


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


# Ключи в lower-case без trim — нормализация в _resolve_interval.
_REPEAT_SELECT_MAP: dict[str, Union[int, dict]] = {
    # daily
    "ежедневно": 1,
    "каждый день": 1,
    "every day": 1,
    "daily": 1,
    # 2 days
    "каждые 2 дня": 2,
    "через день": 2,
    # 3 days
    "каждые 3 дня": 3,
    # weekly
    "еженедельно": 7,
    "каждую неделю": 7,
    "weekly": 7,
    # 2 weeks
    "каждые 2 недели": 14,
    "раз в две недели": 14,
    # monthly
    "ежемесячно": {"kind": "monthly"},
    "каждый месяц": {"kind": "monthly"},
    "monthly": {"kind": "monthly"},
}


def _resolve_interval(
    repeat_time_raw: str,
    repeat_select: str,
) -> Union[int, dict, None]:
    """Источник повторяемости: «Время повтора» (every_Nd) → fallback «Повтор» select.

    Возвращает:
    - int — количество дней между occurrences (1, 7, 14, ...).
    - {"kind": "monthly"} — ежемесячно по anchor.day.
    - None — не повтор.
    """
    _t, days_from_time, _r = _parse_repeat(repeat_time_raw)
    if days_from_time and days_from_time > 0:
        return days_from_time
    if not repeat_select:
        return None
    key = repeat_select.strip().lower()
    return _REPEAT_SELECT_MAP.get(key)


@router.get("/calendar")
async def get_calendar(
    tg_id: int = Depends(current_user_id),
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> dict[str, Any]:
    today_date, tz_offset = await today_user_tz(tg_id)
    if not month:
        month = today_date.strftime("%Y-%m")

    user_notion_id = (await get_user_notion_id(tg_id)) or ""
    raw = await _fetch_tasks_in_month(user_notion_id)

    y, m = int(month[:4]), int(month[5:7])
    days_in_month = _calendar.monthrange(y, m)[1]
    month_start = date(y, m, 1)
    month_end = date(y, m, days_in_month)

    days: dict[str, dict] = {
        str(d): {"count": 0, "has_overdue": False, "has_high_prio": False, "tasks": []}
        for d in range(1, days_in_month + 1)
    }

    for task in raw:
        status = task.status
        if status in ("Archived", "Done", "Complete"):
            continue

        title = task.title
        if not title:
            continue
        prio = prio_from_notion(task.priority)
        cat = cat_from_notion(task.category).get("full") or ""
        repeat_sel = task.repeat or None
        repeat_time_raw = (task.repeat_time or "").strip()
        time_val, _from_time, interval_raw = _parse_repeat(repeat_time_raw)
        repeat_kind = _resolve_interval(repeat_time_raw, repeat_sel or "")
        repeat_label = interval_raw or repeat_sel

        deadline_raw = task.deadline or ""
        reminder_raw = task.reminder or ""
        deadline_date = to_local_date(deadline_raw, tz_offset)
        reminder_date = to_local_date(reminder_raw, tz_offset)
        if time_val is None:
            time_val = extract_time(deadline_raw, tz_offset) or extract_time(reminder_raw, tz_offset)

        # Anchor: Напоминание → Дедлайн → дата создания (последнее — для select-only повторов).
        anchor = reminder_date or deadline_date
        if repeat_kind is not None and anchor is None:
            anchor = to_local_date(getattr(task, "created_at", None), tz_offset)

        occurrence_days: list[int] = []
        is_recurring = repeat_kind is not None
        interval_days_for_overdue: Optional[int] = (
            repeat_kind if isinstance(repeat_kind, int) else None
        )

        if isinstance(repeat_kind, int) and repeat_kind > 0 and anchor:
            interval_days = repeat_kind
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
        elif isinstance(repeat_kind, dict) and repeat_kind.get("kind") == "monthly" and anchor:
            # Ежемесячно по anchor.day. Если anchor.day > days_in_month — берём последний день месяца.
            target_day = min(anchor.day, days_in_month)
            occ = date(y, m, target_day)
            if month_start <= occ <= month_end and occ >= anchor:
                occurrence_days.append(target_day)
        else:
            # wave8.69: показываем задачу и на Дедлайн, и на Напоминание —
            # раньше elif прятал дату напоминания, если дедлайн тоже был в этом месяце.
            if deadline_date and deadline_date.year == y and deadline_date.month == m:
                occurrence_days.append(deadline_date.day)
            if (reminder_date and reminder_date.year == y and reminder_date.month == m
                    and reminder_date.day not in occurrence_days):
                occurrence_days.append(reminder_date.day)

        if not occurrence_days:
            continue

        for day_num in occurrence_days:
            day_date = date(y, m, day_num)
            bucket = days[str(day_num)]
            bucket["tasks"].append({
                "id": task.id,
                "title": title,
                "cat": cat,
                "prio": prio,
                "time": time_val,
                "repeat": repeat_label,
            })
            bucket["count"] += 1
            if day_date < today_date and not is_recurring:
                bucket["has_overdue"] = True
            if prio == "🔴":
                bucket["has_high_prio"] = True

    # Сортировка задач внутри дня: сначала по времени (без времени в конец).
    for bucket in days.values():
        bucket["tasks"].sort(key=lambda t: (t.get("time") is None, t.get("time") or ""))

    ru_info = await _get_ru_month_info(y, m)

    return {
        "month": month,
        "days": days,
        "holiday_days": ru_info["holiday_days"],
        "short_days": ru_info["short_days"],
        "working_weekends": ru_info["working_weekends"],
        "holidays_info": ru_info["holidays_info"],
    }
