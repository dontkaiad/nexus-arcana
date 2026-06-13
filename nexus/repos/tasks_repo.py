"""nexus/repos/tasks_repo.py — repository seam for ✅ Задачи (Tasks)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from core import notion_client as _notion

logger = logging.getLogger("nexus.tasks_repo")


@dataclass
class Task:
    """Domain representation of one ✅ Задачи page."""
    id: str
    title: str
    repeat: str = "Нет"
    repeat_time: str = ""
    deadline_start: str = ""
    reminder_start: str = ""
    archived: bool = False
    raw_props: dict = field(default_factory=dict)


def _parse_task(page: dict) -> Task:
    props = page.get("properties", {})
    title_parts = props.get("Задача", {}).get("title", [])
    title = title_parts[0].get("plain_text", "") if title_parts else ""
    repeat = (props.get("Повтор", {}).get("select") or {}).get("name", "Нет")
    repeat_time_parts = props.get("Время повтора", {}).get("rich_text") or []
    repeat_time = repeat_time_parts[0]["plain_text"].strip() if repeat_time_parts else ""
    deadline_start = ((props.get("Дедлайн", {}).get("date") or {}).get("start", "") or "")
    reminder_start = ((props.get("Напоминание", {}).get("date") or {}).get("start", "") or "")
    return Task(
        id=page.get("id", ""),
        title=title,
        repeat=repeat,
        repeat_time=repeat_time,
        deadline_start=deadline_start,
        reminder_start=reminder_start,
        archived=page.get("archived", False),
        raw_props=props,
    )


class TasksRepo:
    async def active(self, user_notion_id: str = "", include_in_progress: bool = True) -> List[dict]:
        """Return raw Notion pages for all active tasks."""
        return await _notion.tasks_active(
            user_notion_id=user_notion_id,
            include_in_progress=include_in_progress,
        )

    async def retrieve_page(self, page_id: str) -> dict:
        """Fetch a raw Notion page dict by ID. Seals all get_notion().pages.retrieve() calls."""
        client = _notion.get_notion()
        return await client.pages.retrieve(page_id=page_id)

    async def create(self, db_id: str, props: dict) -> Optional[str]:
        """Create a task page and return its page_id, or None on failure."""
        return await _notion.page_create(db_id, props)

    async def set_status(self, page_id: str, status: str) -> bool:
        """Update task status. Returns True on success."""
        return await _notion.update_task_status(page_id, status)

    async def set_in_progress(self, page_id: str) -> None:
        """Set task status to 'In progress'."""
        await _notion.update_page(page_id, {"Статус": _notion._status("In progress")})

    async def set_archived(self, page_id: str) -> None:
        """Set task status to 'Archived'."""
        await _notion.update_page(page_id, {"Статус": _notion._status("Archived")})

    async def set_props(self, page_id: str, props: dict) -> None:
        """Apply an arbitrary props dict to a task page (for complex multi-field updates)."""
        await _notion.update_page(page_id, props)

    async def set_repeat_fields(
        self,
        page_id: str,
        repeat: str,
        day_of_week: Optional[str] = None,
        repeat_time: Optional[str] = None,
    ) -> bool:
        """Update Повтор / День недели / Время повтора fields."""
        return await _notion.update_task_repeat_fields(page_id, repeat, day_of_week, repeat_time)


_repo = TasksRepo()
