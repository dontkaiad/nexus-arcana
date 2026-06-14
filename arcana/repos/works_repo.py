"""arcana/repos/works_repo.py — domain repository for 🔮 Работы.

Pure PG — no Notion calls. Callers receive plain Work dataclass instances.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Work:
    id: str
    title: str
    priority: str
    deadline_str: str
    category_str: str
    has_client: bool


def _pg_repo():
    from arcana.repos.pg_works_repo import PgWorksRepo
    return PgWorksRepo()


class WorksRepo:
    async def list_open(self, user_id: str = "") -> List[Work]:
        return await _pg_repo().list_open(user_notion_id=user_id)

    async def mark_done(self, work_id: str) -> bool:
        return await _pg_repo().mark_done(work_id)
