"""nexus/repos/tasks_repo.py — repository seam for ✅ Задачи (Tasks).

Delegates to PgTasksRepo. Returns fake Notion-format page dicts for handler compat.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("nexus.tasks_repo")

from nexus.repos.pg_tasks_repo import PgTasksRepo as _PgTasksRepo


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
    def __init__(self) -> None:
        self._pg = _PgTasksRepo()

    async def active(self, user_notion_id: str = "", include_in_progress: bool = True) -> List[dict]:
        return await self._pg.active(user_notion_id=user_notion_id,
                                     include_in_progress=include_in_progress)

    async def retrieve_page(self, page_id: str) -> dict:
        return await self._pg.retrieve_page(page_id)

    async def create(self, db_id: str, props: dict) -> Optional[str]:
        return await self._pg.create(db_id, props)

    async def set_status(self, page_id: str, status: str) -> bool:
        return await self._pg.set_status(page_id, status)

    async def set_in_progress(self, page_id: str) -> None:
        await self._pg.set_in_progress(page_id)

    async def set_archived(self, page_id: str) -> None:
        await self._pg.set_archived(page_id)

    async def set_props(self, page_id: str, props: dict) -> None:
        await self._pg.set_props(page_id, props)

    async def list_all(self, user_notion_id: str = "") -> List[dict]:
        return await self._pg.list_all(user_notion_id=user_notion_id)

    async def set_repeat_fields(
        self,
        page_id: str,
        repeat: str,
        day_of_week: Optional[str] = None,
        repeat_time: Optional[str] = None,
    ) -> bool:
        return await self._pg.set_repeat_fields(page_id, repeat, day_of_week, repeat_time)


_repo = TasksRepo()
