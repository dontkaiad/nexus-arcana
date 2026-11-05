"""tests/test_care_schedule.py — уход за котами, основа: период ухода, for_sitter у задач, GET /api/care/schedule,
глушение напоминаний владельцу в период, одно предупреждение о просрочке. Реальная БД, вымышленные данные, токены вымышленные."""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, inspect, select, text

from core import care
from core.config import config
from core.db import get_engine
from miniapp.backend import cats_read_auth as cats_gate
from miniapp.backend import sitter_lists_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from nexus.repos.tasks_tables import care_alerts, care_periods, task_completions, tasks

REPO = Path(__file__).resolve().parent.parent
MARK, MARK2 = "test-care-schedule", "test-care-schedule-other"
TOKEN = "test-only-sitter-" + "a9" * 12
CATS_TOKEN = "test-only-cats-" + "b8" * 12
OWNER = 700000123
OK = {"Authorization": f"Bearer {TOKEN}"}
UTC = timezone.utc


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "sitter_lists_token", TOKEN)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "cats_read_token", CATS_TOKEN)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    monkeypatch.setattr(cats_gate, "_limiter", cats_gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.care.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.care.get_user_tz", AsyncMock(return_value=3)):
        yield
    app.dependency_overrides.pop(current_user_id, None)
    with get_engine().begin() as c:
        c.execute(delete(tasks).where(tasks.c.user_id.in_([MARK, MARK2])))     # completions/alerts уходят каскадом
        c.execute(delete(care_periods).where(care_periods.c.user_id.in_([MARK, MARK2])))


def _status(code="Not started"):
    with get_engine().connect() as c:
        return c.execute(text("SELECT id FROM task_status WHERE code = :c"), {"c": code}).scalar()


def _repeat(code):
    with get_engine().connect() as c:
        return c.execute(text("SELECT id FROM task_repeat WHERE code = :c"), {"c": code}).scalar()


def mk(title="Поменять воду котам", user=MARK, due=None, status="Not started", for_sitter=True, repeat="Ежедневно",
       repeat_time="16:00|every_2d", **kw) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(tasks).values(
            title=title, user_id=user, for_sitter=for_sitter, status_id=_status(status), repeat_id=_repeat(repeat),
            repeat_time=repeat_time, reminder=due if due is not None else datetime.now(UTC) + timedelta(hours=5), **kw
        ).returning(tasks.c.id)).scalar()


def period(user=MARK, a=-1, b=30):
    today = datetime.now(timezone(timedelta(hours=3))).date()
    care.set_period(user, today + timedelta(days=a), today + timedelta(days=b))


def sched(headers=OK):
    return TestClient(app).get("/api/care/schedule", headers=headers)


def owner():
    app.dependency_overrides[current_user_id] = lambda: OWNER
    return TestClient(app)


# ── миграция ──────────────────────────────────────────────────────────────────

def _mig():
    path = next((REPO / "alembic" / "versions").glob("*_care_foundations.py"))
    spec = importlib.util.spec_from_file_location("_mig_care", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_schema_and_partial_unique_on_cycle():
    assert _mig().down_revision == "b9f2d6a1c850"
    insp = inspect(get_engine())
    assert "for_sitter" in {c["name"] for c in insp.get_columns("tasks")}
    assert {"care_periods", "care_alerts"} <= set(insp.get_table_names())
    assert "cycle_due" in {c["name"] for c in insp.get_columns("task_completions")}
    tid, due = mk(), datetime(2026, 12, 1, 13, 0, tzinfo=UTC)
    with get_engine().begin() as c:
        c.execute(insert(task_completions).values(task_id=tid, cycle_due=due))
        c.execute(insert(task_completions).values(task_id=tid))            # без цикла — можно сколько угодно
        c.execute(insert(task_completions).values(task_id=tid))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with get_engine().begin() as c:
            c.execute(insert(task_completions).values(task_id=tid, cycle_due=due))


def test_migration_up_down_reversible_keeps_tasks():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    mig, tid = _mig(), mk()
    with get_engine().begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "for_sitter" not in {c["name"] for c in inspect(conn).get_columns("tasks")}
            assert "care_periods" not in inspect(conn).get_table_names()
            assert conn.execute(text("SELECT count(*) FROM tasks WHERE id = :i"), {"i": tid}).scalar() == 1
            mig.upgrade()


# ── период ────────────────────────────────────────────────────────────────────

def test_period_set_replace_clear_and_active_window():
    today = date(2026, 12, 10)
    care.set_period(MARK, date(2026, 12, 1), date(2026, 12, 31))
    care.set_period(MARK, date(2026, 12, 5), date(2027, 1, 5))               # заменяет, а не добавляет
    assert care.get_period(MARK) == {"starts_on": date(2026, 12, 5), "ends_on": date(2027, 1, 5)}
    with get_engine().connect() as c:
        assert c.execute(select(care_periods.c.id).where(care_periods.c.user_id == MARK)).fetchall().__len__() == 1
    assert care.is_active(MARK, 3, on=today) and care.is_active(MARK, 3, on=date(2026, 12, 5)) and care.is_active(MARK, 3, on=date(2027, 1, 5))
    assert not care.is_active(MARK, 3, on=date(2026, 12, 4)) and not care.is_active(MARK, 3, on=date(2027, 1, 6))
    assert care.clear_period(MARK) is True and care.clear_period(MARK) is False and not care.is_active(MARK, 3, on=today)


@pytest.mark.parametrize("a,b", [(date(2026, 12, 5), date(2026, 12, 4)), (date(2026, 1, 1), date(2027, 6, 1))])
def test_period_validation(a, b):
    with pytest.raises(ValueError):
        care.set_period(MARK, a, b)


def test_period_endpoints_owner_only_and_validated():
    c = owner()
    assert c.get("/api/care/period").json()["period"] is None
    r = c.put("/api/care/period", json={"starts_on": "2026-12-01", "ends_on": "2027-01-31"})
    assert r.json() == {"ok": True, "period": {"starts_on": "2026-12-01", "ends_on": "2027-01-31"}}
    assert c.get("/api/care/period").json()["period"]["ends_on"] == "2027-01-31"
    assert c.put("/api/care/period", json={"starts_on": "2027-02-01", "ends_on": "2027-01-01"}).status_code == 400
    assert c.put("/api/care/period", json={"starts_on": "не дата", "ends_on": "2027-01-01"}).status_code == 422
    assert c.delete("/api/care/period").json() == {"ok": True} and c.delete("/api/care/period").status_code == 404


def test_period_endpoints_reject_sitter_token_and_anonymous():
    for method, kw in (("get", {}), ("put", {"json": {"starts_on": "2026-12-01", "ends_on": "2026-12-02"}}), ("delete", {})):
        for hdr in (OK, {}):
            assert getattr(TestClient(app), method)("/api/care/period", headers=hdr, **kw).status_code in (401, 403)
    assert care.get_period(MARK) is None


# ── флаг задачи ───────────────────────────────────────────────────────────────

def test_task_flag_toggle_own_foreign_missing():
    mine, foreign = mk(for_sitter=False), mk(user=MARK2, for_sitter=False)
    c = owner()
    assert c.post(f"/api/tasks/{mine}/sitter", json={"for_sitter": True}).json() == {"ok": True, "for_sitter": True}
    with get_engine().connect() as conn:
        assert conn.execute(select(tasks.c.for_sitter).where(tasks.c.id == mine)).scalar() is True
    assert c.post(f"/api/tasks/{foreign}/sitter", json={"for_sitter": True}).status_code == 404
    assert c.post("/api/tasks/999999999/sitter", json={"for_sitter": True}).status_code == 404
    with get_engine().connect() as conn:
        assert conn.execute(select(tasks.c.for_sitter).where(tasks.c.id == foreign)).scalar() is False


def test_task_flag_rejects_sitter_token():
    tid = mk(for_sitter=False)
    assert TestClient(app).post(f"/api/tasks/{tid}/sitter", json={"for_sitter": True}, headers=OK).status_code in (401, 403)


def test_tasks_api_exposes_for_sitter():
    from miniapp.backend.routes.tasks import _serialize_pg_task
    from nexus.repos.pg_tasks_repo import Task
    assert _serialize_pg_task(Task(id="1", title="x", for_sitter=True), date(2026, 11, 5), 3)["for_sitter"] is True


# ── расписание ────────────────────────────────────────────────────────────────

def test_schedule_requires_the_sitter_token_only():
    period(); mk()
    assert sched({}).status_code == 401
    assert sched({"Authorization": "Bearer wrong"}).status_code == 401
    assert sched({"Authorization": f"Bearer {CATS_TOKEN}"}).status_code == 401
    r = sched()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    app.dependency_overrides[current_user_id] = lambda: OWNER                     # владельческий вход без токена не пускает
    assert sched({}).status_code == 401


@pytest.mark.parametrize("a,b", [(None, None), (-30, -2), (2, 30)])
def test_no_tasks_outside_the_care_period(a, b):
    mk()
    if a is not None:
        period(a=a, b=b)
    body = sched().json()
    assert body["active"] is False and body["tasks"] == []
    assert body["period"] is None or body["period"]["starts_on"]


def test_active_period_lists_only_own_flagged_open_tasks_with_a_cycle():
    period()
    mk("Вода", due=datetime.now(UTC) + timedelta(hours=2))
    mk("Не для ситтера", for_sitter=False)
    mk("Чужая", user=MARK2)
    mk("Закрытая", status="Done")
    mk("Архивная", status="Archived")
    with get_engine().begin() as c:
        c.execute(insert(tasks).values(title="Без срока", user_id=MARK, for_sitter=True, status_id=_status()))
    body = sched().json()
    assert body["active"] is True and [t["label"] for t in body["tasks"]] == ["Вода"]


def test_schedule_payload_exact_fields_cadence_overdue_last_done_and_order():
    period()
    soon, late = datetime.now(UTC) + timedelta(hours=2), datetime.now(UTC) - timedelta(hours=1)
    a = mk("Поздняя", due=late, repeat="Ежедневно", repeat_time="16:00|every_2d", note="ЗАМЕТКА-СЕКРЕТ", priority_id=None)
    b = mk("Скорая", due=soon, repeat="Еженедельно", repeat_time=None)
    done = datetime.now(UTC) - timedelta(days=2)
    with get_engine().begin() as c:
        c.execute(insert(task_completions).values(task_id=a, done_at=done - timedelta(days=2)))
        c.execute(insert(task_completions).values(task_id=a, done_at=done))
    body = sched().json()
    assert set(body) == {"active", "period", "now", "tasks"} and body["now"].endswith("Z")
    first, second = body["tasks"]
    assert (first["label"], second["label"]) == ("Поздняя", "Скорая")
    assert set(first) == {"id", "label", "cycle_due", "due_at", "postponed", "overdue", "last_done_at", "cadence"}
    assert first["due_at"] == first["cycle_due"] and first["postponed"] is False
    assert first["id"] == str(a) and first["overdue"] is True and first["cadence"] == "каждые 2 дня"
    assert first["last_done_at"].startswith(done.strftime("%Y-%m-%dT%H:%M")) and first["cycle_due"].endswith("Z")
    assert second["overdue"] is False and second["last_done_at"] is None and second["cadence"] == "еженедельно"
    assert "ЗАМЕТКА-СЕКРЕТ" not in str(body) and MARK not in str(body) and b


def test_label_is_clipped_and_cleaned():
    period()
    mk("н" * 300 + "\x1b\x07")
    label = sched().json()["tasks"][0]["label"]
    assert len(label) == 80 and "\x1b" not in label


# ── изоляция токена: он открывает ровно две ручки ────────────────────────────

READ_ROUTES = {("GET", "/api/sitter/lists"), ("GET", "/api/care/schedule")}


def test_sitter_token_opens_exactly_the_read_routes_and_nothing_else():
    client = TestClient(app, raise_server_exceptions=False)
    opened = set()
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api/"):
            continue
        path = r.path
        for name in r.param_convertors if hasattr(r, "param_convertors") else []:
            path = path.replace("{%s}" % name, "1")
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            kw = {"json": {}} if method in ("POST", "PUT", "PATCH", "DELETE") else {}
            with_token = client.request(method, path, headers=OK, **kw).status_code
            without = client.request(method, path, **kw).status_code
            if 200 <= with_token < 300 and not (200 <= without < 300):      # открыто именно токеном (публичные маршруты не в счёт)
                opened.add((method, r.path))
    assert opened == READ_ROUTES, opened ^ READ_ROUTES


# ── глушение напоминаний владельцу ───────────────────────────────────────────

def _t(**kw):
    return SimpleNamespace(**{"for_sitter": True, "user_id": MARK, **kw})


from types import SimpleNamespace  # noqa: E402


def test_is_muted_only_for_flagged_task_in_active_period():
    assert care.is_muted(_t(), 3) is False                                         # периода нет
    period()
    assert care.is_muted(_t(), 3) is True
    assert care.is_muted(_t(for_sitter=False), 3) is False
    assert care.is_muted(_t(user_id=MARK2), 3) is False
    care.clear_period(MARK); period(a=-30, b=-2)
    assert care.is_muted(_t(), 3) is False                                         # период кончился → напоминания вернулись


def test_mute_failure_does_not_silence_the_owner():
    period()
    with patch("core.care.get_period", side_effect=RuntimeError("db down")):
        assert care.is_muted(_t(), 3) is False


async def _fire_reminder(task):
    import nexus.handlers.tasks as tm
    sched_mock, bot = MagicMock(), AsyncMock()
    bot.send_message.return_value = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    when = (datetime.now(timezone(timedelta(hours=3))) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    with patch.object(tm, "_scheduler", sched_mock), patch.object(tm, "_bot", bot), \
         patch.object(tm._repo, "retrieve_page", AsyncMock(return_value=task)), \
         patch.object(tm._repo, "set_in_progress", AsyncMock()) as wip, \
         patch.object(tm, "save_task_reminder", AsyncMock()):
        await tm._schedule_reminder(700000123, task.title, when, task.id, 3, recipients=[700000123])
        job = sched_mock.add_job.call_args.args[0]
        await job()
    return bot, wip


async def test_reminder_is_silent_in_period_and_does_not_touch_status():
    from nexus.repos.pg_tasks_repo import Task
    period()
    bot, wip = await _fire_reminder(Task(id="5", title="Вода", user_id=MARK, for_sitter=True, repeat="Ежедневно"))
    bot.send_message.assert_not_called()
    wip.assert_not_called()


async def test_reminder_still_goes_out_without_period_or_for_other_tasks():
    from nexus.repos.pg_tasks_repo import Task
    bot, wip = await _fire_reminder(Task(id="5", title="Вода", user_id=MARK, for_sitter=True, repeat="Ежедневно"))
    assert bot.send_message.await_count == 1 and wip.await_count == 1             # периода нет
    period()
    bot, _ = await _fire_reminder(Task(id="6", title="Личное", user_id=MARK, for_sitter=False, repeat="Ежедневно"))
    assert bot.send_message.await_count == 1                                       # не задача ситтера


async def test_restore_skips_muted_tasks_in_both_passes():
    import nexus.handlers.tasks as tm
    from nexus.repos.pg_tasks_repo import Task
    period()
    muted = Task(id="7", title="Вода", user_id=MARK, for_sitter=True, repeat="Ежедневно",
                 reminder=(datetime.now(UTC) - timedelta(hours=2)).isoformat())
    future = Task(id="8", title="Вода2", user_id=MARK, for_sitter=True, repeat="Ежедневно",
                  reminder=(datetime.now(UTC) + timedelta(hours=2)).isoformat())

    class FakePg:
        async def active_with_future_reminder(self, uid): return [future]
        async def active_with_past_reminder(self, uid): return [muted]
        async def active_recurring_without_reminder(self, uid): return []
        async def active_with_future_deadline_no_reminder(self, uid): return []

    with patch.object(tm, "_scheduler", MagicMock()), patch.object(tm, "_bot", AsyncMock()), \
         patch("nexus.repos.pg_tasks_repo.PgTasksRepo", FakePg), patch("core.user_manager.get_user", AsyncMock(return_value={"user_id": MARK})), \
         patch.object(tm, "_get_user_tz", AsyncMock(return_value=3)), patch.object(tm, "_schedule_reminder", AsyncMock()) as sr, \
         patch.object(tm._repo, "set_props", AsyncMock()) as sp, patch.object(tm._repo, "set_in_progress", AsyncMock()) as wip:
        await tm.restore_reminders_on_startup()
    sr.assert_not_called(); sp.assert_not_called(); wip.assert_not_called()


# ── предупреждение о просрочке ───────────────────────────────────────────────

def test_overdue_to_alert_rules():
    period()
    now = datetime.now(UTC)
    ok = mk("Просрочена", due=now - timedelta(hours=4))
    mk("Рано", due=now - timedelta(hours=1))                                      # меньше 3 часов
    mk("Не ситтера", due=now - timedelta(hours=9), for_sitter=False)
    mk("Закрыта", due=now - timedelta(hours=9), status="Done")
    assert [x["task_id"] for x in care.overdue_to_alert(now)] == [ok]


def test_overdue_not_alerted_outside_the_period():
    mk("Просрочена", due=datetime.now(UTC) - timedelta(hours=9))
    assert care.overdue_to_alert() == []
    period(a=10, b=40)
    assert care.overdue_to_alert() == []


def test_mark_alerted_is_atomic_once_per_cycle():
    tid, due = mk(), datetime(2026, 12, 1, 13, 0, tzinfo=UTC)
    assert care.mark_alerted(tid, due) is True and care.mark_alerted(tid, due) is False
    assert care.mark_alerted(tid, due + timedelta(days=2)) is True


async def test_alert_sent_once_per_cycle_to_the_owner_only():
    period()
    mk("Просрочена", due=datetime.now(UTC) - timedelta(hours=5))
    with patch("core.bot_notify.notify_user", AsyncMock(return_value=True)) as n, \
         patch("core.user_manager.get_user_id", AsyncMock(return_value=MARK)), patch("core.location.get_user_tz", AsyncMock(return_value=3)):
        assert await care.send_overdue_alerts() == 1
        assert await care.send_overdue_alerts() == 0
    assert n.await_count == 1 and "Просрочена" in n.await_args.args[1]
    with get_engine().connect() as c:
        assert c.execute(select(care_alerts.c.task_id)).fetchall()


async def test_alert_for_a_new_cycle_is_a_new_alert():
    period()
    tid = mk("Вода", due=datetime.now(UTC) - timedelta(hours=5))
    with patch("core.bot_notify.notify_user", AsyncMock(return_value=True)) as n, \
         patch("core.user_manager.get_user_id", AsyncMock(return_value=MARK)), patch("core.location.get_user_tz", AsyncMock(return_value=3)):
        await care.send_overdue_alerts()
        with get_engine().begin() as c:
            c.execute(tasks.update().where(tasks.c.id == tid).values(reminder=datetime.now(UTC) - timedelta(hours=4)))
        await care.send_overdue_alerts()
    assert n.await_count == 2
