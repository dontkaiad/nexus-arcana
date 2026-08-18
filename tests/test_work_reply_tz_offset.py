"""Regression: reply-edit on an Arcana "🔮 Работа" ("перенеси на X" / a
deadline set via reply) wrote the deadline as a naive datetime tagged UTC,
the exact same defect class fixed for Nexus tasks in
tests/test_reply_update.py::test_task_reply_deadline_offset_follows_tz_offset.

PgWorksRepo._set_props_sync parsed the Haiku-returned naive local-wall-clock
string and unconditionally stamped tzinfo=UTC before writing — so a user
whose tz_offset isn't 0 got their work deadline stored shifted, and any
tz-aware reader (Mini App, restore-on-startup) would then display/reschedule
it further shifted by tz_offset on top of that.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from arcana.repos.pg_works_repo import PgWorksRepo


@pytest.mark.asyncio
async def test_set_props_stamps_deadline_with_users_tz_offset():
    conn = MagicMock()
    conn.execute.return_value = MagicMock(rowcount=1)
    engine_cm = MagicMock()
    engine_cm.__enter__.return_value = conn
    engine_cm.__exit__.return_value = False
    engine = MagicMock()
    engine.begin.return_value = engine_cm

    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        ok = await PgWorksRepo().set_props("7", deadline="2026-08-19 11:00", tz_offset=5)

    assert ok is True
    stmt = conn.execute.call_args.args[0]
    deadline_val = stmt._values["deadline"].value
    assert deadline_val.tzinfo == timezone(timedelta(hours=5))
    assert deadline_val.hour == 11  # часы не сдвинуты — только помечены офсетом


@pytest.mark.asyncio
async def test_set_props_default_tz_offset_is_moscow():
    """Без явного tz_offset (старые вызовы) — дефолт +3, не UTC."""
    conn = MagicMock()
    conn.execute.return_value = MagicMock(rowcount=1)
    engine_cm = MagicMock()
    engine_cm.__enter__.return_value = conn
    engine_cm.__exit__.return_value = False
    engine = MagicMock()
    engine.begin.return_value = engine_cm

    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        await PgWorksRepo().set_props("7", deadline="2026-08-19 11:00")

    stmt = conn.execute.call_args.args[0]
    deadline_val = stmt._values["deadline"].value
    assert deadline_val.tzinfo == timezone(timedelta(hours=3))
