"""tests/test_task_done_booking_bridge.py — PgTasksRepo.set_status("Done") →
core.booking.linkage.complete_linked_booking (#23 B6 task→booking bridge).

Regression: Кай отметила "встреча с Mihail Roman" сделанной в Nexus, но
букинг продолжал показывать её занятой — set_status никогда не трогал
booking.status. This is the single chokepoint all 6 "mark Done" call sites
(bot handlers + Mini App) go through, so hooking here covers all of them.

Unit-level: mocks the sync DB write and the linkage call, just verifies the
wiring (call happens exactly when status == "Done" and the write succeeded).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nexus.repos.pg_tasks_repo import PgTasksRepo


@pytest.mark.asyncio
async def test_set_status_done_triggers_booking_completion():
    repo = PgTasksRepo()
    with patch("nexus.repos.pg_tasks_repo._set_status_sync", return_value=True), \
         patch("core.booking.linkage.complete_linked_booking", AsyncMock()) as complete:
        ok = await repo.set_status("87", "Done")
    assert ok is True
    complete.assert_awaited_once_with(nexus_task_id="87")


@pytest.mark.asyncio
async def test_set_status_non_done_does_not_touch_booking():
    repo = PgTasksRepo()
    with patch("nexus.repos.pg_tasks_repo._set_status_sync", return_value=True), \
         patch("core.booking.linkage.complete_linked_booking", AsyncMock()) as complete:
        await repo.set_status("87", "In progress")
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_status_failed_write_does_not_touch_booking():
    """DB-write не удался — не пытаемся синкать бронь на несуществующий результат."""
    repo = PgTasksRepo()
    with patch("nexus.repos.pg_tasks_repo._set_status_sync", return_value=False), \
         patch("core.booking.linkage.complete_linked_booking", AsyncMock()) as complete:
        ok = await repo.set_status("87", "Done")
    assert ok is False
    complete.assert_not_awaited()
