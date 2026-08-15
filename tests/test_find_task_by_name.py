"""tests/test_find_task_by_name.py — issue #152.

core/list_manager.find_task_by_name was a stub that always returned []
(self-documented: "деградировал до пустого результата" after Notion-removal
left no PG equivalent). "Разбей задачу X на подзадачи" / "разбей работу X"
always answered "не найдена" and offered an unlinked checklist instead.

Now dispatches by title_prop ("Работа" -> PgWorksRepo, else -> PgTasksRepo,
matching how nexus/handlers/lists.py and arcana/handlers/lists.py already
called it) and maps results to the plain {"id", "name"} dict shape callers
index into.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.list_manager import find_task_by_name


@pytest.mark.asyncio
async def test_routes_to_tasks_repo_by_default():
    item = SimpleNamespace(id="t1", title="купить корм")
    with patch("nexus.repos.pg_tasks_repo.PgTasksRepo.find_by_title",
               AsyncMock(return_value=[item])) as m_tasks, \
         patch("arcana.repos.pg_works_repo.PgWorksRepo.find_by_title",
               AsyncMock()) as m_works:
        result = await find_task_by_name("корм", "u-1")

    m_tasks.assert_awaited_once_with("корм", user_notion_id="u-1")
    m_works.assert_not_called()
    assert result == [{"id": "t1", "name": "купить корм"}]


@pytest.mark.asyncio
async def test_routes_to_works_repo_for_rabota_title_prop():
    item = SimpleNamespace(id="w1", title="расклад для Оли")
    with patch("arcana.repos.pg_works_repo.PgWorksRepo.find_by_title",
               AsyncMock(return_value=[item])) as m_works, \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo.find_by_title",
               AsyncMock()) as m_tasks:
        result = await find_task_by_name("Оли", "u-1", title_prop="Работа")

    m_works.assert_awaited_once_with("Оли", user_notion_id="u-1")
    m_tasks.assert_not_called()
    assert result == [{"id": "w1", "name": "расклад для Оли"}]


@pytest.mark.asyncio
async def test_no_matches_returns_empty_list():
    with patch("nexus.repos.pg_tasks_repo.PgTasksRepo.find_by_title",
               AsyncMock(return_value=[])):
        result = await find_task_by_name("нет такой", "u-1")
    assert result == []


@pytest.mark.asyncio
async def test_repo_error_is_graceful_not_raised():
    """A DB error must degrade to [] (existing 'not found' UX), not crash
    the handler."""
    with patch("nexus.repos.pg_tasks_repo.PgTasksRepo.find_by_title",
               AsyncMock(side_effect=RuntimeError("boom"))):
        result = await find_task_by_name("x", "u-1")
    assert result == []


@pytest.mark.asyncio
async def test_multiple_matches_all_returned_as_id_name_dicts():
    items = [
        SimpleNamespace(id="t1", title="купить корм коту"),
        SimpleNamespace(id="t2", title="купить корм для рыб"),
    ]
    with patch("nexus.repos.pg_tasks_repo.PgTasksRepo.find_by_title",
               AsyncMock(return_value=items)):
        result = await find_task_by_name("корм", "u-1")
    assert result == [
        {"id": "t1", "name": "купить корм коту"},
        {"id": "t2", "name": "купить корм для рыб"},
    ]


# ── end-to-end через lists_repo.find_task → nexus handle_list_subtask ─────────

@pytest.mark.asyncio
async def test_handle_list_subtask_single_match_sets_pending(mock_message):
    from nexus.handlers import lists as nexus_lists

    msg = mock_message(text="разбей задачу купить корм на подзадачи")
    item = SimpleNamespace(id="t1", title="купить корм коту")

    with patch.object(nexus_lists, "_haiku_parse",
                       AsyncMock(return_value={"task_name": "купить корм"})), \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo.find_by_title",
               AsyncMock(return_value=[item])), \
         patch.object(nexus_lists, "pending_set") as m_pending_set:
        await nexus_lists.handle_list_subtask(msg, {"text": msg.text}, user_notion_id="u-1")

    m_pending_set.assert_called_once()
    uid, state = m_pending_set.call_args.args
    assert state["task_id"] == "t1"
    assert state["task_name"] == "купить корм коту"
    reply = msg.answer.await_args.args[0]
    assert "не найдена" not in reply.lower()
