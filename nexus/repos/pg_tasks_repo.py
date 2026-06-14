"""nexus/repos/pg_tasks_repo.py — PG implementation for nexus ✅ Задачи.

Returns fake Notion-format page dicts so the handler stays unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from nexus.repos.tasks_tables import (
    tasks, task_status, task_repeat, task_day_of_week,
    task_priority, task_category,
)

logger = logging.getLogger("nexus.pg_tasks_repo")

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from arcana.repos.pg_sessions_repo import get_engine as _arc_engine
        _engine = _arc_engine()
    return _engine


# ── Lookup caches (loaded once per process) ────────────────────────────────────

_status_id: Dict[str, int] = {}    # code → id
_status_code: Dict[int, str] = {}  # id → code
_repeat_id: Dict[str, int] = {}
_repeat_code: Dict[int, str] = {}
_dow_id: Dict[str, int] = {}
_dow_code: Dict[int, str] = {}
_priority_id: Dict[str, int] = {}
_priority_code: Dict[int, str] = {}
_category_id: Dict[str, int] = {}
_category_code: Dict[int, str] = {}


def _load_lookups_sync() -> None:
    with get_engine().connect() as conn:
        for row in conn.execute(select(task_status.c.id, task_status.c.code)):
            _status_id[row[1]] = row[0]
            _status_code[row[0]] = row[1]
        for row in conn.execute(select(task_repeat.c.id, task_repeat.c.code)):
            _repeat_id[row[1]] = row[0]
            _repeat_code[row[0]] = row[1]
        for row in conn.execute(select(task_day_of_week.c.id, task_day_of_week.c.code)):
            _dow_id[row[1]] = row[0]
            _dow_code[row[0]] = row[1]
        for row in conn.execute(select(task_priority.c.id, task_priority.c.code)):
            _priority_id[row[1]] = row[0]
            _priority_code[row[0]] = row[1]
        for row in conn.execute(select(task_category.c.id, task_category.c.code)):
            _category_id[row[1]] = row[0]
            _category_code[row[0]] = row[1]


def _ensure_lookups() -> None:
    if not _status_id:
        _load_lookups_sync()


def _match(cache: Dict[str, int], raw: Optional[str], default: Optional[str] = None) -> Optional[int]:
    """Fuzzy match raw string to a lookup id. Checks exact then substring."""
    if not raw:
        if default:
            return cache.get(default)
        return None
    if raw in cache:
        return cache[raw]
    raw_low = raw.lower()
    for code, cid in cache.items():
        if raw_low in code.lower() or code.lower() in raw_low:
            return cid
    if default:
        return cache.get(default)
    return None


def _match_code(cache: Dict[str, int], raw: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """Return the canonical code string matched from raw."""
    if not raw:
        return default
    if raw in cache:
        return raw
    raw_low = raw.lower()
    for code in cache:
        if raw_low in code.lower() or code.lower() in raw_low:
            return code
    return default


# ── Notion-format prop extractors ─────────────────────────────────────────────

def _extract_title(prop: dict) -> str:
    parts = prop.get("title", [])
    if parts:
        p = parts[0]
        return p.get("plain_text") or p.get("text", {}).get("content", "")
    return ""


def _extract_select(prop: dict) -> str:
    sel = prop.get("select") or {}
    return sel.get("name", "")


def _extract_status(prop: dict) -> str:
    sel = prop.get("status") or {}
    return sel.get("name", "")


def _extract_date(prop: dict) -> str:
    d = prop.get("date") or {}
    return d.get("start", "")


def _extract_text(prop: dict) -> str:
    parts = prop.get("rich_text", [])
    if parts:
        p = parts[0]
        return p.get("plain_text") or p.get("text", {}).get("content", "")
    return ""


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string (with or without TZ) to datetime."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ── Fake Notion-format page builder ───────────────────────────────────────────

def _to_notion_page(row) -> dict:
    """Convert a DB row (joined with lookups) to a fake Notion page dict."""
    status_c = _status_code.get(row.status_id, "Not started")
    repeat_c = _repeat_code.get(row.repeat_id, "Нет") if row.repeat_id else "Нет"
    dow_c = _dow_code.get(row.day_of_week_id) if row.day_of_week_id else None
    priority_c = _priority_code.get(row.priority_id, "🟡 Важно") if row.priority_id else "🟡 Важно"
    category_c = _category_code.get(row.category_id, "💳 Прочее") if row.category_id else "💳 Прочее"

    repeat_time_list = [{"plain_text": row.repeat_time}] if row.repeat_time else []

    def _date_prop(dt: Optional[datetime]) -> Optional[dict]:
        if dt is None:
            return None
        return {"start": dt.isoformat()}

    props = {
        "Задача": {"title": [{"plain_text": row.title, "text": {"content": row.title}}]},
        "Статус": {"status": {"name": status_c}},
        "Повтор": {"select": {"name": repeat_c} if repeat_c else None},
        "День недели": {"select": {"name": dow_c} if dow_c else None},
        "Приоритет": {"select": {"name": priority_c}},
        "Категория": {"select": {"name": category_c}},
        "Время повтора": {"rich_text": repeat_time_list},
        "Дедлайн": {"date": _date_prop(row.deadline)},
        "Напоминание": {"date": _date_prop(row.reminder)},
        "Время завершения": {"date": _date_prop(row.completed_at)},
    }
    updated = getattr(row, "updated_at", None)
    last_edited = updated.isoformat() if updated else ""
    return {
        "id": str(row.id),
        "archived": False,
        "last_edited_time": last_edited,
        "properties": props,
    }


# ── Sync helpers (run in asyncio.to_thread) ───────────────────────────────────

def _list_active_sync(user_notion_id: str, include_in_progress: bool) -> List[dict]:
    _ensure_lookups()
    q = select(tasks).where(
        tasks.c.status_id.notin_(
            select(task_status.c.id).where(
                task_status.c.code.in_(["Done", "Archived"])
            )
        )
    )
    if not include_in_progress:
        q = q.where(
            tasks.c.status_id != select(task_status.c.id).where(
                task_status.c.code == "In progress"
            ).scalar_subquery()
        )
    if user_notion_id:
        q = q.where(tasks.c.user_notion_id == user_notion_id)
    q = q.order_by(tasks.c.priority_id.asc().nulls_last())

    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_to_notion_page(r) for r in rows]


def _get_sync(task_id: str) -> Optional[dict]:
    _ensure_lookups()
    try:
        pid = int(task_id)
    except (ValueError, TypeError):
        return None
    with get_engine().connect() as conn:
        row = conn.execute(
            select(tasks).where(tasks.c.id == pid)
        ).fetchone()
    if row is None:
        return None
    return _to_notion_page(row)


def _list_all_sync(user_notion_id: str) -> List[dict]:
    """Return ALL tasks (including Done/Archived) for stats."""
    _ensure_lookups()
    q = select(tasks).order_by(tasks.c.updated_at.desc())
    if user_notion_id:
        q = q.where(tasks.c.user_notion_id == user_notion_id)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_to_notion_page(r) for r in rows]


def _create_sync(
    title: str,
    status: str,
    priority: Optional[str],
    category: Optional[str],
    deadline: Optional[str],
    reminder: Optional[str],
    user_notion_id: str,
) -> Optional[int]:
    _ensure_lookups()
    status_id = _match(_status_id, status, "Not started")
    if status_id is None:
        status_id = _status_id.get("Not started")
    vals = {
        "title": title,
        "status_id": status_id,
        "priority_id": _match(_priority_id, priority, "🟡 Важно"),
        "category_id": _match(_category_id, category, "💳 Прочее"),
        "deadline": _parse_iso(deadline),
        "reminder": _parse_iso(reminder),
        "user_notion_id": user_notion_id or "",
    }
    with get_engine().begin() as conn:
        result = conn.execute(tasks.insert().values(**vals).returning(tasks.c.id))
        return result.fetchone()[0]


def _set_status_sync(task_id: str, status: str) -> bool:
    _ensure_lookups()
    sid = _match(_status_id, status)
    if sid is None:
        return False
    try:
        with get_engine().begin() as conn:
            conn.execute(
                tasks.update()
                .where(tasks.c.id == int(task_id))
                .values(status_id=sid, updated_at=text("now()"))
            )
        return True
    except Exception as e:
        logger.error("_set_status_sync: %s", e)
        return False


def _set_props_sync(task_id: str, props: dict) -> None:
    """Parse Notion-format props dict and apply to PG."""
    _ensure_lookups()
    vals: dict = {"updated_at": text("now()")}

    for field, prop in props.items():
        if field == "Задача":
            vals["title"] = _extract_title(prop)
        elif field == "Статус":
            raw = _extract_status(prop)
            sid = _match(_status_id, raw)
            if sid:
                vals["status_id"] = sid
        elif field == "Приоритет":
            raw = _extract_select(prop)
            pid = _match(_priority_id, raw)
            if pid:
                vals["priority_id"] = pid
        elif field == "Категория":
            raw = _extract_select(prop)
            cid = _match(_category_id, raw)
            if cid:
                vals["category_id"] = cid
        elif field == "Повтор":
            raw = _extract_select(prop)
            rid = _match(_repeat_id, raw)
            if rid is not None:
                vals["repeat_id"] = rid
        elif field == "День недели":
            raw = _extract_select(prop)
            did = _match(_dow_id, raw) if raw else None
            vals["day_of_week_id"] = did
        elif field == "Время повтора":
            vals["repeat_time"] = _extract_text(prop) or None
        elif field == "Дедлайн":
            vals["deadline"] = _parse_iso(_extract_date(prop))
        elif field == "Напоминание":
            vals["reminder"] = _parse_iso(_extract_date(prop))
        elif field == "Время завершения":
            vals["completed_at"] = _parse_iso(_extract_date(prop))

    if len(vals) <= 1:
        return
    with get_engine().begin() as conn:
        conn.execute(
            tasks.update()
            .where(tasks.c.id == int(task_id))
            .values(**vals)
        )


def _set_repeat_fields_sync(
    task_id: str,
    repeat: str,
    day_of_week: Optional[str],
    repeat_time: Optional[str],
) -> bool:
    _ensure_lookups()
    vals: dict = {"updated_at": text("now()")}
    rid = _match(_repeat_id, repeat)
    if rid:
        vals["repeat_id"] = rid
    if day_of_week:
        did = _match(_dow_id, day_of_week)
        if did:
            vals["day_of_week_id"] = did
    if repeat_time:
        vals["repeat_time"] = repeat_time
    try:
        with get_engine().begin() as conn:
            conn.execute(
                tasks.update()
                .where(tasks.c.id == int(task_id))
                .values(**vals)
            )
        return True
    except Exception as e:
        logger.error("_set_repeat_fields_sync: %s", e)
        return False


# Active tasks with future reminder (for restore_reminders pass 1) ─────────────

def _active_with_future_reminder_sync(user_notion_id: str) -> List[dict]:
    _ensure_lookups()
    done_ids = select(task_status.c.id).where(
        task_status.c.code.in_(["Done", "Archived"])
    )
    q = (
        select(tasks)
        .where(tasks.c.status_id.notin_(done_ids))
        .where(tasks.c.reminder > text("now()"))
    )
    if user_notion_id:
        q = q.where(tasks.c.user_notion_id == user_notion_id)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_to_notion_page(r) for r in rows]


def _active_with_past_reminder_sync(user_notion_id: str) -> List[dict]:
    _ensure_lookups()
    done_ids = select(task_status.c.id).where(
        task_status.c.code.in_(["Done", "Archived"])
    )
    q = (
        select(tasks)
        .where(tasks.c.status_id.notin_(done_ids))
        .where(tasks.c.reminder < text("now()"))
        .where(tasks.c.reminder.isnot(None))
    )
    if user_notion_id:
        q = q.where(tasks.c.user_notion_id == user_notion_id)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_to_notion_page(r) for r in rows]


# ── Public async API ───────────────────────────────────────────────────────────

class PgTasksRepo:
    async def active(
        self, user_notion_id: str = "", include_in_progress: bool = True
    ) -> List[dict]:
        return await asyncio.to_thread(
            _list_active_sync, user_notion_id, include_in_progress
        )

    async def retrieve_page(self, page_id: str) -> dict:
        page = await asyncio.to_thread(_get_sync, page_id)
        return page or {}

    async def create(
        self,
        _db_id: str,
        props: dict,
    ) -> Optional[str]:
        title = _extract_title(props.get("Задача", {}))
        status = _extract_status(props.get("Статус", {})) or "Not started"
        priority = _extract_select(props.get("Приоритет", {}))
        category = _extract_select(props.get("Категория", {}))
        deadline = _extract_date(props.get("Дедлайн", {}))
        reminder = _extract_date(props.get("Напоминание", {}))
        user_notion_id = ""
        rel = props.get("🪪 Пользователи", {})
        if rel:
            parts = rel.get("relation", [])
            if parts:
                user_notion_id = parts[0].get("id", "")

        pid = await asyncio.to_thread(
            _create_sync, title, status, priority, category,
            deadline, reminder, user_notion_id,
        )
        return str(pid) if pid else None

    async def set_status(self, page_id: str, status: str) -> bool:
        return await asyncio.to_thread(_set_status_sync, page_id, status)

    async def set_in_progress(self, page_id: str) -> None:
        await asyncio.to_thread(_set_status_sync, page_id, "In progress")

    async def set_archived(self, page_id: str) -> None:
        await asyncio.to_thread(_set_status_sync, page_id, "Archived")

    async def set_props(self, page_id: str, props: dict) -> None:
        await asyncio.to_thread(_set_props_sync, page_id, props)

    async def set_repeat_fields(
        self,
        page_id: str,
        repeat: str,
        day_of_week: Optional[str] = None,
        repeat_time: Optional[str] = None,
    ) -> bool:
        return await asyncio.to_thread(
            _set_repeat_fields_sync, page_id, repeat, day_of_week, repeat_time
        )

    async def list_all(self, user_notion_id: str = "") -> List[dict]:
        """Return all tasks (Done/Archived included) for stats."""
        return await asyncio.to_thread(_list_all_sync, user_notion_id)

    async def active_with_future_reminder(self, user_notion_id: str = "") -> List[dict]:
        return await asyncio.to_thread(_active_with_future_reminder_sync, user_notion_id)

    async def active_with_past_reminder(self, user_notion_id: str = "") -> List[dict]:
        return await asyncio.to_thread(_active_with_past_reminder_sync, user_notion_id)

    async def get_by_notion_id(self, notion_id: str) -> Optional[dict]:
        """Find task by Notion page ID (for backfill cross-reference)."""
        def _sync():
            _ensure_lookups()
            with get_engine().connect() as conn:
                row = conn.execute(
                    select(tasks).where(tasks.c.notion_id == notion_id)
                ).fetchone()
            return _to_notion_page(row) if row else None
        return await asyncio.to_thread(_sync)
