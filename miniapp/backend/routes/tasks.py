"""miniapp/backend/routes/tasks.py — GET /api/tasks."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

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

logger = logging.getLogger("miniapp.tasks")

router = APIRouter()

ALLOWED_FILTERS = {"all", "active", "overdue", "done"}

_PRIO_WEIGHT = {"🔴": 0, "🟡": 1, "⚪": 2}


def _date_start(prop: dict) -> str:
    d = prop.get("date") or {}
    return d.get("start") or ""


def _parse_iso(s: str):
    from datetime import datetime, timezone
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _compute_reminder_min(deadline_raw: str, reminder_raw: str) -> Optional[int]:
    if not (deadline_raw and reminder_raw):
        return None
    dl = _parse_iso(deadline_raw)
    rm = _parse_iso(reminder_raw)
    if not (dl and rm):
        return None
    delta = (dl - rm).total_seconds() / 60
    return int(round(delta)) if delta > 0 else None


def _serialize_task(page: dict, today_date, tz_offset: int) -> dict:
    props = page.get("properties", {})
    deadline_raw = _date_start(props.get("Дедлайн", {}))
    deadline_local = to_local_date(deadline_raw, tz_offset)
    deadline_time = extract_time(deadline_raw, tz_offset)
    status = status_name(props.get("Статус", {}))
    repeat_time = rich_text(props.get("Время повтора", {})).strip() or None

    if status in ("Done", "Complete"):
        computed_status = "done"
    elif deadline_local and deadline_local < today_date:
        computed_status = "overdue"
    else:
        computed_status = "active"

    return {
        "id": page.get("id", ""),
        "title": title_text(props.get("Задача", {})),
        "cat": cat_from_notion(select_name(props.get("Категория", {}))),
        "prio": prio_from_notion(select_name(props.get("Приоритет", {}))),
        "status": computed_status,
        "deadline": deadline_local.isoformat() if deadline_local else None,
        "deadline_time": deadline_time,
        "repeat": select_name(props.get("Повтор", {})) or None,
        "repeat_time": repeat_time,
        "reminder_min": _compute_reminder_min(
            deadline_raw, _date_start(props.get("Напоминание", {}))
        ),
        "streak": None,  # per-task streak не хранится — см. wave 2a спеку
    }


def _build_filter(filter_name: str, user_notion_id: str, today_iso: str) -> dict:
    """Notion-фильтр для разных filter_name значений."""
    base: list[dict] = [{"property": "Бот", "select": {"equals": BOT_NEXUS}}]
    if user_notion_id:
        base.append({
            "property": "🪪 Пользователи",
            "relation": {"contains": user_notion_id},
        })

    if filter_name == "done":
        base.append({"property": "Статус", "status": {"equals": "Done"}})
    elif filter_name == "overdue":
        base.extend([
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Complete"}},
            {"property": "Дедлайн", "date": {"before": today_iso}},
        ])
    elif filter_name == "active":
        # Active = не-Done и (deadline >= today OR deadline is null) — часть условий
        # придётся проверять в Python (Notion не умеет "deadline >= today OR null"
        # одним фильтром без `or`). Берём "не Done" + client-side фильтр по дате.
        base.extend([
            {"property": "Статус", "status": {"does_not_equal": "Done"}},
            {"property": "Статус", "status": {"does_not_equal": "Complete"}},
        ])
    # "all" — только Бот + user; ничего не исключаем (кроме ниже Archived, если оно есть как статус)
    return {"and": base}


@router.get("/tasks")
async def get_tasks(
    tg_id: int = Depends(current_user_id),
    filter: str = Query("active", description="all|active|overdue|done"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    if filter not in ALLOWED_FILTERS:
        raise HTTPException(status_code=400, detail=f"filter must be one of {sorted(ALLOWED_FILTERS)}")

    today_date, tz_offset = await today_user_tz(tg_id)
    today_iso = today_date.isoformat()
    user_notion_id = (await get_user_notion_id(tg_id)) or ""

    filters = _build_filter(filter, user_notion_id, today_iso)

    if filter == "done":
        sorts = [{"property": "Время завершения", "direction": "descending"}]
    else:
        sorts = [{"property": "Дедлайн", "direction": "ascending"}]

    try:
        raw = await query_pages(
            config.nexus.db_tasks, filters=filters, sorts=sorts, page_size=limit,
        )
    except Exception as e:
        logger.warning("tasks query failed, retrying without sort: %s", e)
        raw = await query_pages(
            config.nexus.db_tasks, filters=filters, page_size=limit,
        )

    items = [_serialize_task(p, today_date, tz_offset) for p in raw]

    # Client-side фильтр для 'active': оставляем только те, у которых status == 'active'
    # (отсекает overdue задачи, которые по Notion-статусу ещё "Not started")
    if filter == "active":
        items = [t for t in items if t["status"] == "active"]

    if filter in ("active", "overdue"):
        def _prio_key(t):
            w = _PRIO_WEIGHT.get(t["prio"], 99)
            d = t["deadline"] or "9999-99-99"
            return (w, d)
        items.sort(key=_prio_key)

    return {
        "filter": filter,
        "total": len(items),
        "tasks": items[:limit],
    }
