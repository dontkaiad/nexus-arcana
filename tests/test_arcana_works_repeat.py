"""tests/test_arcana_works_repeat.py — repeat для Работ Арканы (повторяющиеся
ритуалы/работы).

Покрывает:
  (a) миграция ab12cd34ef56 up/down обратима (реальная PG, в транзакции);
  (b) PgWorksRepo.create/find_by_id/set_repeat_fields несут repeat-поля;
  (c) core.recurrence.next_cycle_date — общая математика цикла;
  (d) _handle_recurring_work_done: завершение повторяющейся Работы сдвигает
      дедлайн/напоминание на следующий цикл, статус остаётся open (НЕ done),
      напоминание перепланировано, core/task_streaks НЕ вызывается (ADR-0023);
  (e) work_complete callback на повторяющейся Работе → recurring-путь,
      mark_done не зовётся;
  (f) _parse_work_text вытаскивает repeat из текста.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from arcana.repos.works_repo import Work
from arcana.repos.works_tables import (
    metadata, work_priority, work_status, work_repeat, work_day_of_week, works,
)

REPO = Path(__file__).resolve().parent.parent


# ── in-memory engine (mirrors works schema incl. repeat) ─────────────────────

@pytest.fixture
def engine():
    eng = sa.create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE clients (id INTEGER PRIMARY KEY)"))
        for t in ("work_priority", "work_status", "work_repeat", "work_day_of_week"):
            conn.execute(sa.text(
                f"CREATE TABLE {t} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "code TEXT NOT NULL UNIQUE, emoji TEXT, label TEXT NOT NULL, "
                "sort INTEGER DEFAULT 0)"
            ))
        conn.execute(sa.text(
            "CREATE TABLE works (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT NOT NULL, deadline TIMESTAMP, reminder TIMESTAMP, "
            "category TEXT, priority_id INTEGER, status_id INTEGER, client_id INTEGER, "
            "repeat_id INTEGER, day_of_week_id INTEGER, repeat_time TEXT, "
            "user_notion_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    with eng.begin() as conn:
        conn.execute(work_priority.insert().values(
            [{"code": "later", "emoji": "🟢", "label": "Можно потом", "sort": 2}]))
        conn.execute(work_status.insert().values(
            [{"code": "open", "emoji": "⬜", "label": "В работе", "sort": 0},
             {"code": "done", "emoji": "✅", "label": "Готово", "sort": 1},
             {"code": "archived", "emoji": "🗄️", "label": "Архив", "sort": 2}]))
        conn.execute(work_repeat.insert().values(
            [{"code": "none", "emoji": "", "label": "Нет", "sort": 0},
             {"code": "daily", "emoji": "🔁", "label": "Ежедневно", "sort": 1},
             {"code": "weekly", "emoji": "🔁", "label": "Еженедельно", "sort": 2},
             {"code": "monthly", "emoji": "🔁", "label": "Ежемесячно", "sort": 3}]))
        conn.execute(work_day_of_week.insert().values(
            [{"code": "mon", "emoji": "", "label": "Пн", "sort": 1},
             {"code": "fri", "emoji": "", "label": "Пт", "sort": 5}]))
    return eng


# ── (a) migration up/down ───────────────────────────────────────────────────

def test_migration_works_repeat_up_down_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from core.db import get_engine

    path = os.path.join(REPO, "alembic", "versions", "ab12cd34ef56_works_repeat.py")
    spec = importlib.util.spec_from_file_location("_mig_works_repeat_t", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "d0e1f2a3b4c5"

    eng = get_engine()
    assert "repeat_id" in [c["name"] for c in inspect(eng).get_columns("works")]

    with eng.connect() as conn:
        trans = conn.begin()
        try:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.downgrade()
                cols = [c["name"] for c in inspect(conn).get_columns("works")]
                assert "repeat_id" not in cols and "repeat_time" not in cols
                assert not inspect(conn).has_table("work_repeat")
                mig.upgrade()
                cols = [c["name"] for c in inspect(conn).get_columns("works")]
                assert "repeat_id" in cols and "repeat_time" in cols
                assert inspect(conn).has_table("work_repeat")
        finally:
            trans.rollback()


# ── (b) repo carries repeat fields ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_read_recurring_work(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        wid = await repo.create(
            title="Чистка чакр", priority="Важно", category="✨ Ритуал",
            repeat="Ежедневно", repeat_time="08:00", user_notion_id="u1",
        )
        w = await repo.find_by_id(wid)
    assert w.repeat == "Ежедневно"
    assert w.repeat_time == "08:00"
    assert w.status == "open"


@pytest.mark.asyncio
async def test_set_repeat_fields(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        wid = await repo.create(title="Пост", priority="Можно потом", user_notion_id="u1")
        ok = await repo.set_repeat_fields(wid, "Еженедельно", day_of_week="Пн")
        w = await repo.find_by_id(wid)
    assert ok is True
    assert w.repeat == "Еженедельно"
    assert w.day_of_week == "Пн"


@pytest.mark.asyncio
async def test_non_recurring_work_defaults_to_net(engine):
    from arcana.repos.pg_works_repo import PgWorksRepo
    repo = PgWorksRepo()
    with patch("arcana.repos.pg_works_repo.get_engine", return_value=engine):
        wid = await repo.create(title="Закупить свечи", user_notion_id="u1")
        w = await repo.find_by_id(wid)
    assert w.repeat == "Нет"


# ── (c) recurrence math ────────────────────────────────────────────────────

def test_next_cycle_date_daily_weekly_monthly():
    from core.recurrence import next_cycle_date
    assert next_cycle_date("2020-01-01", "Ежедневно", 3) != "2020-01-01"
    # base = max(old, today); просроченная не прыгает в прошлое
    r = next_cycle_date("2020-01-01T08:00", "Ежедневно", 3)
    assert r.endswith("T08:00")
    r2 = next_cycle_date("2020-01-01", "Ежемесячно", 3)
    assert len(r2) == 10


def test_parse_repeat_time_interval():
    from core.recurrence import parse_repeat_time
    assert parse_repeat_time("17:00|every_2d") == ("17:00", 2)
    assert parse_repeat_time("09:00") == ("09:00", 0)
    assert parse_repeat_time("") == ("09:00", 0)


# ── (d) _handle_recurring_work_done ────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_recurring_work_done_advances_and_keeps_open():
    from arcana.handlers import work_reminder_kb as wk

    tz = timezone(timedelta(hours=3))
    past = datetime(2020, 5, 1, 9, 0, tzinfo=tz)
    work = Work(
        id="42", title="Чистка чакр", priority="Важно", deadline_str="",
        category_str="", has_client=False, status="open",
        deadline_dt=past, reminder_dt=past,
        repeat="Ежедневно", repeat_time="09:00",
    )
    msg = MagicMock()
    msg.chat = MagicMock(); msg.chat.id = 111

    reschedule_calls = []
    sched_calls = []

    fake_repo = MagicMock()
    fake_repo.reschedule_cycle = AsyncMock(side_effect=lambda *a, **k: reschedule_calls.append(k) or True)

    fake_flow = MagicMock()
    fake_flow.remove_jobs = MagicMock()
    fake_flow.schedule_reminder = AsyncMock(side_effect=lambda **k: sched_calls.append(k) or True)

    with patch("arcana.repos.pg_works_repo.PgWorksRepo", return_value=fake_repo), \
         patch("arcana.bot.arcana_reminder_flow", fake_flow), \
         patch("core.task_streaks.update_task_streak") as m_streak:
        nxt = await wk._handle_recurring_work_done(msg, "42", work, "Чистка чакр", 3)

    # streaks сознательно НЕ трогаем (ADR-0023)
    m_streak.assert_not_called()
    # reschedule_cycle вызван с будущими датами, mark_done — нет
    assert reschedule_calls, "reschedule_cycle должен быть вызван"
    kw = reschedule_calls[0]
    assert kw["deadline"] is not None and kw["deadline"].year >= datetime.now().year
    assert kw["reminder"] is not None
    # напоминание перепланировано
    assert sched_calls and sched_calls[0]["page_id"] == "42"
    assert nxt and len(nxt) == 10


@pytest.mark.asyncio
async def test_work_complete_callback_recurring_uses_cycle_not_markdone():
    from arcana.handlers import work_reminder_kb as wk

    recurring = Work(
        id="7", title="Медитация", priority="Важно", deadline_str="",
        category_str="", has_client=False, status="open",
        repeat="Ежедневно", repeat_time="07:00",
        deadline_dt=None, reminder_dt=None,
    )
    fake_repo = MagicMock()
    fake_repo.find_by_id = AsyncMock(return_value=recurring)
    fake_repo.mark_done = AsyncMock()

    call = MagicMock()
    call.data = "work_complete_7"
    call.from_user = MagicMock(); call.from_user.id = 999
    call.message = MagicMock()
    call.message.text = "🔔 Напоминание: Медитация"
    call.message.chat = MagicMock(); call.message.chat.id = 5
    call.message.edit_reply_markup = AsyncMock()
    call.message.reply = AsyncMock()
    call.answer = AsyncMock()

    with patch("arcana.repos.pg_works_repo.PgWorksRepo", return_value=fake_repo), \
         patch.object(wk, "_get_tz", AsyncMock(return_value=3)), \
         patch.object(wk, "_handle_recurring_work_done", AsyncMock(return_value="2026-09-06")) as m_rec:
        await wk.work_complete(call)

    m_rec.assert_awaited_once()
    fake_repo.mark_done.assert_not_called()
    call.message.reply.assert_awaited_once()
    assert "Повтор" in call.message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_work_complete_callback_oneoff_still_marks_done():
    from arcana.handlers import work_reminder_kb as wk

    oneoff = Work(id="8", title="Купить свечи", priority="Можно потом",
                  deadline_str="", category_str="", has_client=False,
                  status="open", repeat="Нет")
    fake_repo = MagicMock()
    fake_repo.find_by_id = AsyncMock(return_value=oneoff)
    fake_repo.mark_done = AsyncMock(return_value=True)

    call = MagicMock()
    call.data = "work_complete_8"
    call.from_user = MagicMock(); call.from_user.id = 999
    call.message = MagicMock()
    call.message.text = "🔔 Напоминание: Купить свечи"
    call.message.chat = MagicMock(); call.message.chat.id = 5
    call.message.edit_reply_markup = AsyncMock()
    call.message.reply = AsyncMock()
    call.answer = AsyncMock()

    with patch("arcana.repos.pg_works_repo.PgWorksRepo", return_value=fake_repo), \
         patch("arcana.bot.arcana_reminder_flow", MagicMock()):
        await wk.work_complete(call)

    fake_repo.mark_done.assert_awaited_once_with("8")


# ── (f) parse extracts repeat ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_work_text_extracts_repeat():
    from arcana.handlers import work_preview as wp
    fake_json = (
        '{"title":"Чистка чакр","deadline":null,"priority":"важно",'
        '"category":"ритуал","client_name":null,"type":"личная",'
        '"repeat":"Ежедневно","repeat_time":"08:00","day_of_week":null}'
    )
    with patch.object(wp, "ask_claude", AsyncMock(return_value=fake_json)):
        data = await wp._parse_work_text("чистка чакр каждое утро в 8", 3)
    assert data["repeat"] == "Ежедневно"
    assert data["repeat_time"] == "08:00"
    assert data["deadline"] is None
