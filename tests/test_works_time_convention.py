"""#267: Арканa пишет deadline/reminder работ по соглашению Nexus —
локальное настенное время + пояс пользователя → настоящий UTC в PG."""
from __future__ import annotations

import os
import tempfile
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_TMP_DB = tempfile.NamedTemporaryFile(suffix="_pending_works_tzconv.db", delete=False).name
import arcana.handlers.work_preview as wp  # noqa: E402
wp._PENDING_DB = _TMP_DB


def _utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


class _FakeEngine:
    """Ловит UPDATE works SET reminder=... (параметры compile())."""
    def __init__(self):
        self.params = []

    def begin(self):
        eng = self

        class _Ctx:
            def __enter__(self_inner):
                conn = MagicMock()
                conn.execute.side_effect = lambda stmt: eng.params.append(stmt.compile().params)
                return conn

            def __exit__(self_inner, *a):
                return False
        return _Ctx()


@pytest.mark.asyncio
async def test_cb_work_save_writes_real_utc_for_deadline_and_reminder():
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    slug = wp._make_slug(42)
    wp._pending_set(42, slug, {
        "title": "Ритуал", "category": "✨ Ритуал", "priority": "Срочно",
        "work_type": "🌟 Личная", "client_id": None,
        "deadline": "2026-11-01T19:45", "reminder": "2026-11-01T19:30",
        "msg_id": 999, "chat_id": 100, "user_id": "u",
    })
    call = MagicMock()
    call.from_user.id = 42
    call.data = f"work_save:{slug}"
    call.message = MagicMock()
    call.message.chat.id = 100
    call.message.message_id = 999
    call.message.edit_text = AsyncMock()
    call.message.answer = AsyncMock()
    call.answer = AsyncMock()

    flow = MagicMock()
    flow.schedule_reminder = AsyncMock(return_value=True)
    fake_bot = MagicMock(arcana_reminder_flow=flow)
    eng = _FakeEngine()

    with patch.object(wp._works_repo, "create", AsyncMock(return_value="5")) as create, \
         patch.dict("sys.modules", {"arcana.bot": fake_bot}), \
         patch("arcana.handlers.work_preview.get_user_tz", AsyncMock(return_value=3)), \
         patch("core.db.get_engine", lambda: eng), \
         patch("core.user_manager.get_tg_ids_for_user", AsyncMock(return_value=[1])), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await wp.cb_work_save(call)

    deadline = create.await_args.kwargs["deadline"]
    assert deadline.tzinfo is not None
    assert _utc(deadline) == "2026-11-01T16:45"  # 19:45 МСК = 16:45 UTC
    assert eng.params, "reminder UPDATE не выполнен"
    assert _utc(eng.params[0]["reminder"]) == "2026-11-01T16:30"  # 19:30 МСК
    # а планировщику уходит локальное настенное время (как и раньше)
    assert flow.schedule_reminder.await_args.kwargs["reminder_dt"] == "2026-11-01T19:30"


def test_row_to_work_shows_local_time_for_utc_deadline():
    from datetime import datetime
    from types import SimpleNamespace
    from arcana.repos.pg_works_repo import _row_to_work
    row = SimpleNamespace(
        id=5, title="t", deadline=datetime(2026, 11, 1, 16, 45, tzinfo=timezone.utc),
        reminder=None, category="", notes="", client_id=None, repeat_time="",
        priority_label="Срочно", status_code="open", repeat_label="Нет", dow_label="",
    )
    assert _row_to_work(row).deadline_str.endswith("01.11 19:45")


def test_relative_time_shared_with_core():
    import arcana.handlers.work_reminder_kb as kb
    from core.date_extract import parse_relative_time
    assert kb._parse_relative_time is parse_relative_time
