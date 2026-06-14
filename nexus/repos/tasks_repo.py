"""nexus/repos/tasks_repo.py — repository seam for ✅ Задачи (Tasks)."""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("nexus.tasks_repo")

from nexus.repos.pg_tasks_repo import PgTasksRepo as _PgTasksRepo, Task  # noqa: F401 — re-export Task


class TasksRepo:
    def __init__(self) -> None:
        self._pg = _PgTasksRepo()

    async def active(self, user_notion_id: str = "", include_in_progress: bool = True) -> List[Task]:
        return await self._pg.active(user_notion_id=user_notion_id,
                                     include_in_progress=include_in_progress)

    async def retrieve_page(self, page_id: str) -> Optional[Task]:
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

    async def list_all(self, user_notion_id: str = "") -> List[Task]:
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
