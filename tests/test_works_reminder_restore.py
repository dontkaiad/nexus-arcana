"""tests/test_works_reminder_restore.py — restore Work-напоминаний на старте Арканы.

Покрытие:
- PgWorksRepo.active_with_future_reminder: возвращает только не-done/не-archived
  Работы с reminder в будущем (done/archived/прошлые/null исключены).
- restore_work_reminders: пере-планирует через arcana_reminder_flow тем же
  ключом (page_id=str(work.id)), что использует cb_work_save.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import BigInteger, Column, Table, create_engine, event
from sqlalchemy.pool import StaticPool

from arcana.repos.rituals_tables import metadata
from arcana.repos.works_tables import works, work_status, work_priority
from arcana.repos.works_repo import Work
import arcana.repos.pg_works_repo as pgw


# ── filter test (реальный SQL на in-memory sqlite) ───────────────────────────

@pytest.fixture
def works_engine(monkeypatch):
    # works.client_id → FK на clients (другая metadata) — добавим stub, чтобы
    # create_all смог собрать works; уберём после теста.
    clients_stub = Table("clients", metadata, Column("id", BigInteger, primary_key=True))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, _rec):
        # sqlite не знает now(); код использует text("now()"). Фиксируем «сейчас».
        dbapi_conn.create_function("now", 0, lambda: "2026-06-19 00:00:00.000000")

    try:
        metadata.create_all(engine, tables=[work_status, work_priority, clients_stub, works])
        with engine.begin() as c:
            for sid, code in [(1, "open"), (2, "done"), (3, "archived")]:
                c.execute(work_status.insert().values(id=sid, code=code, label=code))
            c.execute(work_priority.insert().values(id=1, code="later", label="Можно потом"))
            fut = datetime(2026, 6, 20, 17, 0)
            past = datetime(2026, 6, 18, 9, 0)
            c.execute(works.insert().values(id=10, title="future-open",   status_id=1, priority_id=1, reminder=fut))
            c.execute(works.insert().values(id=11, title="future-done",   status_id=2, priority_id=1, reminder=fut))
            c.execute(works.insert().values(id=12, title="future-arch",   status_id=3, priority_id=1, reminder=fut))
            c.execute(works.insert().values(id=13, title="past-open",     status_id=1, priority_id=1, reminder=past))
            c.execute(works.insert().values(id=14, title="null-open",     status_id=1, priority_id=1, reminder=None))
        monkeypatch.setattr(pgw, "get_engine", lambda: engine)
        yield engine
    finally:
        metadata.remove(clients_stub)


@pytest.mark.asyncio
async def test_active_with_future_reminder_filters(works_engine):
    res = await pgw.PgWorksRepo().active_with_future_reminder("")
    ids = sorted(int(w.id) for w in res)
    # только открытая работа с будущим reminder
    assert ids == [10]
    # done(11)/archived(12)/прошлый(13)/null(14) — исключены
    assert 11 not in ids and 12 not in ids and 13 not in ids and 14 not in ids


# ── restore wiring test (ключ джобы = ключ cb_work_save) ─────────────────────

@pytest.mark.asyncio
async def test_restore_reschedules_with_matching_key():
    import arcana.bot as abot
    from core.config import config

    w = Work(
        id="42", title="Расклад Оле", priority="Можно потом",
        deadline_str="", category_str="", has_client=True,
        reminder_dt=datetime(2026, 12, 31, 17, 0),
    )

    fake_user = {"permissions": {"arcana": True}, "notion_page_id": "u-1"}

    with patch.object(config, "allowed_ids", [7]), \
         patch("core.user_manager.get_user", AsyncMock(return_value=fake_user)), \
         patch("core.shared_handlers.get_user_tz", AsyncMock(return_value=3)), \
         patch.object(pgw.PgWorksRepo, "active_with_future_reminder",
                      AsyncMock(return_value=[w])), \
         patch.object(abot.arcana_reminder_flow, "schedule_reminder",
                      AsyncMock(return_value=True)) as m_sched:
        n = await abot.restore_work_reminders()

    assert n == 1
    m_sched.assert_awaited_once()
    kwargs = m_sched.await_args.kwargs
    # КЛЮЧ: page_id = str(work.id) — тот же, что ставит cb_work_save → reminder_{id}
    assert kwargs["page_id"] == "42"
    assert kwargs["chat_id"] == 7
    assert kwargs["tz_offset"] == 3
    assert kwargs["reminder_dt"] == "2026-12-31T17:00"


@pytest.mark.asyncio
async def test_restore_skips_non_arcana_user():
    import arcana.bot as abot
    from core.config import config

    with patch.object(config, "allowed_ids", [7]), \
         patch("core.user_manager.get_user",
               AsyncMock(return_value={"permissions": {"arcana": False}})), \
         patch.object(pgw.PgWorksRepo, "active_with_future_reminder",
                      AsyncMock(return_value=[])) as m_q, \
         patch.object(abot.arcana_reminder_flow, "schedule_reminder",
                      AsyncMock()) as m_sched:
        n = await abot.restore_work_reminders()

    assert n == 0
    m_q.assert_not_called()
    m_sched.assert_not_called()
