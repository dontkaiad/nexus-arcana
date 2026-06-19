"""tests/test_checklist_parent_pg.py — checklist parent ✅ Задача создаётся в PG.

ListsRepo.add_checklist_task: pg_tasks_repo.create (PG), НЕ Notion task_add.
Используется только Nexus-чеклистами; Arcana subtasks (subtasks_handler) этот
путь не зовёт (пишет list-items с relation на существующую работу).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nexus.repos.pg_tasks_repo import _extract_title, _extract_status, _extract_select


@pytest.mark.asyncio
async def test_add_checklist_task_creates_in_pg():
    from core.repos.lists_repo import ListsRepo

    repo = ListsRepo()
    with patch("nexus.repos.tasks_repo._repo.create", AsyncMock(return_value="pg-77")) as m_create, \
         patch("core.notion_client.task_add", AsyncMock(return_value="should-not-be-used")) as m_taskadd:
        result = await repo.add_checklist_task("Покупки на дачу", user_notion_id="u-1")

    assert result == "pg-77"
    m_create.assert_awaited_once()
    props = m_create.call_args.args[1]
    assert _extract_title(props["Задача"]) == "Покупки на дачу"
    assert _extract_status(props["Статус"]) == "Not started"
    assert _extract_select(props["Приоритет"]) == "Важно"
    assert _extract_select(props["Категория"]) == "💳 Прочее"
    assert props["🪪 Пользователи"]["relation"][0]["id"] == "u-1"
    m_taskadd.assert_not_called()


@pytest.mark.asyncio
async def test_add_checklist_task_no_user_omits_relation():
    from core.repos.lists_repo import ListsRepo

    repo = ListsRepo()
    with patch("nexus.repos.tasks_repo._repo.create", AsyncMock(return_value="pg-1")) as m_create, \
         patch("core.notion_client.task_add", AsyncMock()):
        await repo.add_checklist_task("Чеклист", user_notion_id="")
    props = m_create.call_args.args[1]
    assert "🪪 Пользователи" not in props
