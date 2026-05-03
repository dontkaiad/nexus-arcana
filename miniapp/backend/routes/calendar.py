"""miniapp/backend/routes/calendar.py — GET /api/calendar."""
from __future__ import annotations

import calendar as _calendar
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Union

try:
    import holidays as _holidays_pkg
except ImportError:
    _holidays_pkg = None

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
    """3 параллельных запроса + dedup по page["id"].

    Notion API лимит — 2 уровня вложенности фильтров. Раньше было
    `and → or → and` = 3 уровня → Notion отвергал, и query_pages молча
    возвращал []. Теперь — три отдельных запроса (каждый с одним `and`):
      1) Дедлайн ∈ [start, end]
      2) Напоминание ∈ [start, end]
      3) «Время повтора» непустое
    Результаты сливаются по `page["id"]`.
    """
    import asyncio

    user_filter: list[dict] = []
    if user_notion_id:
        user_filter.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })

    deadline_filter = {"and": user_filter + [
        {"property": "Дедлайн", "date": {"on_or_after": start}},
        {"property": "Дедлайн", "date": {"on_or_before": end}},
    ]}
    reminder_filter = {"and": user_filter + [
        {"property": "Напоминание", "date": {"on_or_after": start}},
        {"property": "Напоминание", "date": {"on_or_before": end}},
    ]}
    recurring_filter = {"and": user_filter + [
        {"property": "Время повтора", "rich_text": {"is_not_empty": True}},
    ]}

    db_id = config.nexus.db_tasks
    deadline_pages, reminder_pages, recurring_pages = await asyncio.gather(
        query_pages(db_id, filters=deadline_filter, page_size=500),
        query_pages(db_id, filters=reminder_filter, page_size=500),
        query_pages(db_id, filters=recurring_filter, page_size=500),
    )

    seen: dict[str, dict] = {}
    for bucket in (deadline_pages, reminder_pages, recurring_pages):
        for p in bucket:
            pid = p.get("id")
            if pid and pid not in seen:
                seen[pid] = p
    return list(seen.values())


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


def _page_created_date(page: dict, tz_offset: int) -> Optional[date]:
    raw = page.get("created_time") or ""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=tz_offset))).date()


def _ru_holiday_days(year: int, month_num: int) -> list[int]:
    """Дни месяца (числа), которые являются официальными праздниками РФ.

    holidays-пакет уже учитывает переносы выходных. Если пакет не установлен —
    возвращаем пустой список (бэкенд работает, фронт не подсветит).
    """
    if _holidays_pkg is None:
        return []
    try:
        h = _holidays_pkg.RU(years=year)
    except Exception as e:
        logger.warning("holidays.RU init failed: %s", e)
        return []
    out: list[int] = []
    for d in h.keys():
        if d.year == year and d.month == month_num:
            out.append(d.day)
    return sorted(set(out))


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
        time_val, _from_time, interval_raw = _parse_repeat(repeat_time_raw)
        repeat_kind = _resolve_interval(repeat_time_raw, repeat_sel or "")
        repeat_label = interval_raw or repeat_sel

        deadline_raw = (props.get("Дедлайн", {}).get("date") or {}).get("start") or ""
        reminder_raw = (props.get("Напоминание", {}).get("date") or {}).get("start") or ""
        deadline_date = to_local_date(deadline_raw, tz_offset)
        reminder_date = to_local_date(reminder_raw, tz_offset)
        if time_val is None:
            time_val = extract_time(deadline_raw, tz_offset) or extract_time(reminder_raw, tz_offset)

        # Anchor: Напоминание → Дедлайн → created_time (последний — для select-only повторов).
        anchor = reminder_date or deadline_date
        if repeat_kind is not None and anchor is None:
            anchor = _page_created_date(p, tz_offset)

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
                "id": p.get("id", ""),
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

    holiday_days = _ru_holiday_days(y, m)

    return {"month": month, "days": days, "holiday_days": holiday_days}
