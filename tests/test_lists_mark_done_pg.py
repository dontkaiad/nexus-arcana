"""tests/test_lists_mark_done_pg.py — ListsRepo.mark_task_done на PG (Notion-removal).

mark_task_done больше не зовёт Notion update_task_status — статус задачи
ставится через PgTasksRepo.set_status(id, 'Done').
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_mark_task_done_uses_pg_set_status():
    from core.repos.lists_repo import ListsRepo
    from nexus.repos.tasks_repo import _repo as _tasks_repo
    with patch.object(_tasks_repo, "set_status", AsyncMock(return_value=True)) as m:
        ok = await ListsRepo().mark_task_done("42")
    m.assert_awaited_once_with("42", "Done")
    assert ok is True
