"""tests/test_done_multi_streak_task_id.py — issue #116.

cb_done_multi_confirm iterated ALL candidate tasks shown to the user (not
just the selected/completed ones) and, after the loop, called
_update_streak_line(uid, task_id) using whatever `task_id` the for-loop
last bound — the last candidate in the original list, regardless of
whether it was selected or successfully marked done. That id gets written
to streaks.last_task_id/last_task_at (shown in the Mini App streak sheet),
so completing an earlier-listed task while a later-listed one sat unselected
recorded the wrong "last completed" task.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_streak_uses_actually_completed_task_not_last_candidate():
    import nexus.handlers.tasks as tasks_mod
    from nexus.repos.pg_tasks_repo import Task

    uid = 67686090
    task_a = Task(id="a", title="задача A", repeat="Нет")
    task_b = Task(id="b", title="задача B", repeat="Нет")  # selected + done
    task_c = Task(id="c", title="задача C", repeat="Нет")  # last in list, NOT selected

    tasks_mod._done_multi_tasks[uid] = [
        (1, task_a.title, task_a.id, task_a),
        (1, task_b.title, task_b.id, task_b),
        (1, task_c.title, task_c.id, task_c),
    ]
    tasks_mod._done_multi_selected[uid] = {"b"}

    call = AsyncMock()
    call.from_user = MagicMock(id=uid)
    call.message = AsyncMock()

    with patch.object(tasks_mod._repo, "set_status", AsyncMock(return_value=True)) as m_status, \
         patch.object(tasks_mod, "_remove_task_jobs", MagicMock()), \
         patch.object(tasks_mod, "_update_streak_line", AsyncMock(return_value="")) as m_streak:
        await tasks_mod.cb_done_multi_confirm(call)
        # Only task B was selected/completed — streak must be recorded
        # against B, not C (which was merely last in the candidate list).
        m_streak.assert_awaited_once_with(uid, "b")
        m_status.assert_awaited_once_with("b", "Done")
