"""Regression test: marking a task done/cancelled via the Mini App must cancel
the still-pending APScheduler reminder_/deadline_ jobs — not just gracefully
no-op if a chat message was never sent (#73 only covered the latter).

Bug: task_done/task_cancel updated the DB status and (for done) tried to edit
an already-sent reminder message via clear_task_reminder, but never called
nexus.handlers.tasks._remove_task_jobs, so a task completed/cancelled BEFORE
its reminder/deadline fired still got pinged later.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from nexus.repos.pg_tasks_repo import Task as PgTask

FAKE_TG_ID = 67686090
FAKE_NOTION_USER = "user-notion-id-42"


def _client():
    app.dependency_overrides[current_user_id] = lambda: FAKE_TG_ID
    return TestClient(app)


def test_task_done_one_shot_cancels_scheduler_jobs():
    task = PgTask(id="task-1", title="такси в Гай", user_notion_id=FAKE_NOTION_USER, repeat="Нет", repeat_time="")
    remove_jobs = MagicMock()
    try:
        with patch("miniapp.backend.routes.writes.clear_task_reminder", AsyncMock(return_value=False)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
                   AsyncMock(return_value=task)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
                   AsyncMock(return_value=True)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.set_props",
                   AsyncMock(return_value=None)), \
             patch("miniapp.backend.routes.writes.get_user_notion_id",
                   AsyncMock(return_value=FAKE_NOTION_USER)), \
             patch("nexus.handlers.streaks.update_streak", AsyncMock(return_value=None)), \
             patch("nexus.handlers.tasks._remove_task_jobs", remove_jobs):
            r = _client().post("/api/tasks/task-1/done")
        assert r.status_code == 200
        remove_jobs.assert_called_once_with("task-1")
    finally:
        app.dependency_overrides.clear()


def test_task_done_recurring_does_not_cancel_scheduler_jobs():
    """Recurring task done (reminder step) → In progress, waiting for the
    deadline stage — the still-pending deadline job must stay armed."""
    task = PgTask(id="task-2", title="полить цветы", user_notion_id=FAKE_NOTION_USER,
                  repeat="Ежедневно", repeat_time="10:00")
    remove_jobs = MagicMock()
    try:
        with patch("miniapp.backend.routes.writes.clear_task_reminder", AsyncMock(return_value=False)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
                   AsyncMock(return_value=task)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
                   AsyncMock(return_value=True)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.set_props",
                   AsyncMock(return_value=None)), \
             patch("miniapp.backend.routes.writes.get_user_notion_id",
                   AsyncMock(return_value=FAKE_NOTION_USER)), \
             patch("core.task_streaks.update_task_streak", MagicMock()), \
             patch("nexus.handlers.streaks.update_streak", AsyncMock(return_value=None)), \
             patch("nexus.handlers.tasks._remove_task_jobs", remove_jobs):
            r = _client().post("/api/tasks/task-2/done")
        assert r.status_code == 200
        remove_jobs.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_task_cancel_cancels_scheduler_jobs():
    task = PgTask(id="task-3", title="забронировать такси", user_notion_id=FAKE_NOTION_USER)
    remove_jobs = MagicMock()
    try:
        with patch("miniapp.backend.routes.writes.clear_task_reminder", AsyncMock(return_value=False)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.retrieve_page",
                   AsyncMock(return_value=task)), \
             patch("miniapp.backend.routes.writes._tasks_pg_repo.set_status",
                   AsyncMock(return_value=True)), \
             patch("miniapp.backend.routes.writes.get_user_notion_id",
                   AsyncMock(return_value=FAKE_NOTION_USER)), \
             patch("nexus.handlers.tasks._remove_task_jobs", remove_jobs):
            r = _client().post("/api/tasks/task-3/cancel")
        assert r.status_code == 200
        remove_jobs.assert_called_once_with("task-3")
    finally:
        app.dependency_overrides.clear()
