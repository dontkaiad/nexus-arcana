"""miniapp/backend/routes/tasks.py — GET /api/tasks (PG-native, Notion-free)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.user_manager import get_user_notion_id
from miniapp.backend.auth import current_user_id
from miniapp.backend._helpers import (
    cat_from_notion,
    extract_time,
    prio_from_notion,
    to_local_date,
    today_user_tz,
)
from nexus.repos.pg_tasks_repo import PgTasksRepo, Task as PgTask

logger = logging.getLogger("miniapp.tasks")

router = APIRouter()
_tasks_repo = PgTasksRepo()

ALLOWED_FILTERS = {"all", "active", "overdue", "done", "today"}

_PRIO_WEIGHT = {"🔴": 0, "🟡": 1, "⚪": 2}


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


def _serialize_pg_task(task: PgTask, today_date, tz_offset: int) -> dict:
    deadline_raw = task.deadline or ""
    deadline_local = to_local_date(deadline_raw, tz_offset)
    deadline_time = extract_time(deadline_raw, tz_offset)
    reminder_raw = task.reminder or ""
    reminder_time = extract_time(reminder_raw, tz_offset)
    status = task.status

    if status in ("Done", "Complete"):
        computed_status = "done"
    elif status == "Archived":
        computed_status = "cancelled"
    elif deadline_local and deadline_local < today_date:
        computed_status = "overdue"
    else:
        computed_status = "active"

    closed_at = None
    if computed_status in ("done", "cancelled"):
        closed_at = task.completed_at or task.last_edited or None

    repeat = task.repeat if task.repeat not in ("Нет", "", None) else None

    return {
        "id": task.id,
        "title": task.title,
        "cat": cat_from_notion(task.category),
        "prio": prio_from_notion(task.priority),
        "status": computed_status,
        "deadline": deadline_local.isoformat() if deadline_local else None,
        "deadline_time": deadline_time,
        "reminder_time": reminder_time,
        "reminder_iso": reminder_raw or None,
        "deadline_iso": deadline_raw or None,
        "repeat": repeat,
        "repeat_time": task.repeat_time or None,
        "reminder_min": _compute_reminder_min(deadline_raw, reminder_raw),
        "closed_at": closed_at,
        "streak": None,
    }


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

    if filter in ("done", "all"):
        raw = await _tasks_repo.list_all(user_notion_id)
    else:
        raw = await _tasks_repo.active(user_notion_id)

    items = [_serialize_pg_task(t, today_date, tz_offset) for t in raw]

    if filter == "done":
        items = [t for t in items if t["status"] in ("done", "cancelled")]
    elif filter == "active":
        items = [t for t in items if t["status"] == "active"]
    elif filter == "overdue":
        items = [t for t in items if t["status"] == "overdue"]
    elif filter == "today":
        items = [
            t for t in items
            if t["status"] in ("active", "overdue")
            and t["deadline"] and t["deadline"] <= today_iso
        ]

    if filter in ("all", "active", "overdue", "today"):
        def _sort_key(t):
            closed = t.get("status") in ("done", "cancelled")
            soonest = min(
                x for x in (t.get("deadline_iso"), t.get("reminder_iso"))
                if x
            ) if (t.get("deadline_iso") or t.get("reminder_iso")) else "9999-99-99"
            w = _PRIO_WEIGHT.get(t["prio"], 99)
            return (closed, soonest, w)
        items.sort(key=_sort_key)
    elif filter == "done":
        items.sort(key=lambda t: (t.get("closed_at") or ""), reverse=True)

    return {
        "filter": filter,
        "total": len(items),
        "tasks": items[:limit],
    }
