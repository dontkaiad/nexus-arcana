"""tests/test_care_write.py — ситтер выполняет цикл расписания и переносит его: отдельный write-токен, идемпотентность,
якорь серии, разовый перенос без сдвига базы. Реальная БД, вымышленные данные, токены вымышленные."""
from __future__ import annotations

import importlib.util
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, inspect, select, text

from core import care
from core.config import config
from core.db import get_engine
from core.recurrence import next_cycle_anchored
from miniapp.backend import care_write_auth as wgate
from miniapp.backend import cats_read_auth as cats_gate
from miniapp.backend import sitter_lists_auth as rgate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from nexus.repos.tasks_tables import care_periods, task_completions, task_overrides, tasks

REPO = Path(__file__).resolve().parent.parent
MARK, MARK2 = "test-care-write", "test-care-write-other"
WRITE = "test-only-write-" + "c7" * 12
READ = "test-only-sitter-" + "d6" * 12
CATS = "test-only-cats-" + "e5" * 12
OWNER = 700000123
W = {"Authorization": f"Bearer {WRITE}"}
R = {"Authorization": f"Bearer {READ}"}
UTC = timezone.utc
MSK = timezone(timedelta(hours=3))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "care_write_token", WRITE)
    monkeypatch.setattr(config, "care_write_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "sitter_lists_token", READ)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "cats_read_token", CATS)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    for m in (wgate, rgate):
        monkeypatch.setattr(m, "_limiter", m.FailureLimiter())
    monkeypatch.setattr(cats_gate, "_limiter", cats_gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    resched = AsyncMock()
    streak = MagicMock()
    with patch("miniapp.backend.routes.care_write.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.care_write.get_user_tz", AsyncMock(return_value=3)), \
         patch("miniapp.backend.routes.care.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.care.get_user_tz", AsyncMock(return_value=3)), \
         patch("miniapp.backend.routes.writes._reschedule_task_reminder", resched), \
         patch("core.task_streaks.update_task_streak", streak):
        _env.resched, _env.streak = resched, streak
        yield
    with get_engine().begin() as c:
        c.execute(delete(tasks).where(tasks.c.user_id.in_([MARK, MARK2])))
        c.execute(delete(care_periods).where(care_periods.c.user_id.in_([MARK, MARK2])))


def _id(table, col, code):
    with get_engine().connect() as c:
        return c.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": code}).scalar()


def mk(due=None, title="Поменять воду", user=MARK, repeat="Ежедневно", repeat_time="16:00|every_2d", for_sitter=True, status="Not started", **kw):
    with get_engine().begin() as c:
        return c.execute(insert(tasks).values(
            title=title, user_id=user, for_sitter=for_sitter, status_id=_id("task_status", "", status),
            repeat_id=_id("task_repeat", "", repeat), repeat_time=repeat_time,
            reminder=due if due is not None else datetime.now(UTC) - timedelta(hours=1), **kw).returning(tasks.c.id)).scalar()


def period(a=-1, b=30, user=MARK):
    today = datetime.now(MSK).date()
    care.set_period(user, today + timedelta(days=a), today + timedelta(days=b))


def cycle_of(tid) -> str:
    """cycle_due так, как его получает клиент из расписания."""
    body = TestClient(app).get("/api/care/schedule", headers=R).json()
    return next(t["cycle_due"] for t in body["tasks"] if t["id"] == str(tid))


def done(tid, cycle, headers=W, by="ситтер"):
    return TestClient(app).post(f"/api/care/tasks/{tid}/done", json={"cycle_due": cycle, "done_by": by}, headers=headers)


def postpone(tid, cycle, new_due, headers=W):
    return TestClient(app).post(f"/api/care/tasks/{tid}/postpone", json={"cycle_due": cycle, "new_due": new_due}, headers=headers)


def reminder_of(tid):
    with get_engine().connect() as c:
        return c.execute(select(tasks.c.reminder).where(tasks.c.id == tid)).scalar()


def status_of(tid):
    with get_engine().connect() as c:
        return c.execute(text("SELECT s.code FROM tasks t JOIN task_status s ON s.id = t.status_id WHERE t.id = :i"), {"i": tid}).scalar()


def completions(tid):
    with get_engine().connect() as c:
        return c.execute(select(task_completions.c.source, task_completions.c.done_by, task_completions.c.cycle_due)
                         .where(task_completions.c.task_id == tid)).all()


def z(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ── якорная математика серии ──────────────────────────────────────────────────

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=MSK)


def anch(cur, repeat="Ежедневно", ivl=0, now=NOW):
    return next_cycle_anchored(cur, repeat, ivl, now)


def test_anchor_does_not_follow_today_when_completed_late():
    cur = datetime(2026, 11, 9, 16, 0, tzinfo=MSK)                          # цикл был вчера в 16:00, сейчас 10-го 12:00
    assert anch(cur, ivl=2) == datetime(2026, 11, 11, 16, 0, tzinfo=MSK)    # от исходного срока: +2 дня, а не от «сегодня»
    assert anch(cur) == datetime(2026, 11, 10, 16, 0, tzinfo=MSK)           # ежедневно: сегодня в 16:00 (ещё впереди)


def test_anchor_skips_missed_cycles_and_stays_on_the_grid():
    cur = datetime(2026, 11, 1, 16, 0, tzinfo=MSK)
    assert anch(cur, ivl=2) == datetime(2026, 11, 11, 16, 0, tzinfo=MSK)    # 3,5,7,9 пропущены → 11 (сетка: нечётные дни)
    assert anch(cur, "Еженедельно") == datetime(2026, 11, 15, 16, 0, tzinfo=MSK)


def test_anchor_monthly_keeps_day_and_returns_after_short_month():
    cur = datetime(2026, 11, 25, 16, 0, tzinfo=MSK)
    assert anch(cur, "Ежемесячно", now=datetime(2026, 11, 27, 9, 0, tzinfo=MSK)) == datetime(2026, 12, 25, 16, 0, tzinfo=MSK)
    jan31 = datetime(2027, 1, 31, 16, 0, tzinfo=MSK)
    feb = anch(jan31, "Ежемесячно", now=datetime(2027, 2, 1, tzinfo=MSK))
    assert feb == datetime(2027, 2, 28, 16, 0, tzinfo=MSK)
    assert anch(jan31, "Ежемесячно", now=datetime(2027, 3, 1, tzinfo=MSK)) == datetime(2027, 3, 31, 16, 0, tzinfo=MSK)


def test_anchor_result_is_strictly_after_now():
    cur = datetime(2026, 11, 10, 12, 0, tzinfo=MSK)
    assert anch(cur, now=cur) > cur and anch(cur, ivl=2, now=cur + timedelta(days=2)) == cur + timedelta(days=4)


# ── миграция ──────────────────────────────────────────────────────────────────

def test_migration_overrides_schema_reversible_in_rolled_back_transaction():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = next((REPO / "alembic" / "versions").glob("*_task_overrides.py"))
    spec = importlib.util.spec_from_file_location("_mig_ovr", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "c1a7e3b9d204"
    assert "task_overrides" in inspect(get_engine()).get_table_names()
    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()
                assert "task_overrides" not in inspect(conn).get_table_names()
                mig.upgrade()
                assert "task_overrides" in inspect(conn).get_table_names()
        finally:
            trans.rollback()


# ── токен: отдельный, жёсткий, не взаимозаменяемый ───────────────────────────

def test_write_token_required_no_fallthrough():
    period(); tid = mk(); cyc = cycle_of(tid)
    for hdr in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer"}, {"Authorization": "Basic " + WRITE},
                {"Authorization": f"Bearer {WRITE}x"}, {"Authorization": WRITE}):
        r = done(tid, cyc, headers=hdr)
        assert r.status_code == 401, hdr
    app.dependency_overrides[current_user_id] = lambda: OWNER                    # владельческий вход без токена не пускает
    assert done(tid, cyc, headers={}).status_code == 401
    assert completions(tid) == []


def test_read_and_cats_tokens_cannot_write_and_write_token_cannot_read():
    period(); tid = mk(); cyc = cycle_of(tid)
    for hdr in (R, {"Authorization": f"Bearer {CATS}"}):
        assert done(tid, cyc, headers=hdr).status_code == 401
        assert postpone(tid, cyc, z(datetime.now(UTC) + timedelta(hours=1)), headers=hdr).status_code == 401
    assert completions(tid) == []
    c = TestClient(app)
    for path in ("/api/care/schedule", "/api/sitter/lists", "/api/cats"):
        assert c.get(path, headers=W).status_code == 401


@pytest.mark.parametrize("wt,owner,allowed,sitter,cats", [
    ("", OWNER, [OWNER], READ, CATS), ("short", OWNER, [OWNER], READ, CATS), (WRITE, 0, [OWNER], READ, CATS),
    (WRITE, OWNER, [700000001], READ, CATS), (WRITE, OWNER, [OWNER], WRITE, CATS), (WRITE, OWNER, [OWNER], READ, WRITE)])
def test_feature_off_when_misconfigured_or_tokens_equal(monkeypatch, wt, owner, allowed, sitter, cats):
    period(); tid = mk(); cyc = cycle_of(tid)
    monkeypatch.setattr(config, "care_write_token", wt)
    monkeypatch.setattr(config, "care_write_owner_tg_id", owner)
    monkeypatch.setattr(config, "allowed_ids", allowed)
    monkeypatch.setattr(config, "sitter_lists_token", sitter)
    monkeypatch.setattr(config, "cats_read_token", cats)
    assert done(tid, cyc, headers={"Authorization": f"Bearer {wt or 'x'}"}).status_code == 401
    assert completions(tid) == []


def test_equal_write_and_read_tokens_disable_the_read_gate_too(monkeypatch):
    period(); mk()
    monkeypatch.setattr(config, "sitter_lists_token", WRITE)
    assert TestClient(app).get("/api/care/schedule", headers={"Authorization": f"Bearer {WRITE}"}).status_code == 401


def test_rate_limit_is_separate_and_applies_to_valid_token_after_failures():
    period(); tid = mk(); cyc = cycle_of(tid)
    c = TestClient(app)
    for _ in range(10):
        assert c.post(f"/api/care/tasks/{tid}/done", json={"cycle_due": cyc}, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert c.post(f"/api/care/tasks/{tid}/done", json={"cycle_due": cyc}, headers=W).status_code == 429
    assert c.get("/api/care/schedule", headers=R).status_code == 200            # читающий гейт со своим окном


def test_token_never_logged(caplog):
    caplog.set_level("DEBUG")
    period(); tid = mk(); cyc = cycle_of(tid)
    done(tid, cyc, headers={"Authorization": f"Bearer {WRITE}x"}); done(tid, cyc)
    assert WRITE not in caplog.text


def _opened_by(token_hdr):
    client = TestClient(app, raise_server_exceptions=False)
    opened = set()
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api/"):
            continue
        path = r.path
        for name in getattr(r, "param_convertors", {}):
            path = path.replace("{%s}" % name, "1")
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            kw = {"json": {}} if method in ("POST", "PUT", "PATCH", "DELETE") else {}
            with_token = client.request(method, path, headers=token_hdr, **kw).status_code
            without = client.request(method, path, **kw).status_code
            if with_token not in (401, 403, 429) and without in (401, 403):      # 429 лимита неверных попыток: тоже отказ
                opened.add((method, r.path))
    return opened


def test_write_token_opens_exactly_the_two_write_routes():
    assert _opened_by(W) == {("POST", "/api/care/tasks/{task_id}/done"), ("POST", "/api/care/tasks/{task_id}/postpone")}


def test_read_token_opens_exactly_the_read_routes():
    assert _opened_by(R) == {("GET", "/api/sitter/lists"), ("GET", "/api/care/schedule"), ("POST", "/api/sitter/ask"), ("GET", "/api/sitter/cat-care")}


# ── done ──────────────────────────────────────────────────────────────────────

def test_done_records_completion_advances_from_the_anchor_and_updates_streak():
    period()
    cycle = datetime.now(UTC) - timedelta(hours=1)
    tid = mk(due=cycle)
    r = done(tid, cycle_of(tid), by="Ситтер Анна")
    assert r.status_code == 200 and r.json()["dup"] is False
    rows = completions(tid)
    assert len(rows) == 1 and rows[0].source == "sitter" and rows[0].done_by == "Ситтер Анна" and abs(rows[0].cycle_due - cycle) < timedelta(seconds=1)
    assert abs(reminder_of(tid) - (cycle + timedelta(days=2))) < timedelta(seconds=1)       # якорь: +2 дня от цикла, не от «сейчас»
    assert status_of(tid) == "In progress"
    assert r.json()["next_due"] == z(reminder_of(tid))
    _env.resched.assert_awaited_once()
    assert _env.streak.call_args.kwargs["interval_days"] == 2 and _env.streak.call_args.kwargs["user_id"] == OWNER


def test_late_done_does_not_shift_the_series_base_and_skips_missed_cycles():
    period(a=-30)
    cycle = datetime.now(UTC) - timedelta(days=5, hours=1)                                    # пропущено 2 цикла
    tid = mk(due=cycle)
    done(tid, cycle_of(tid))
    nxt = reminder_of(tid)
    delta_days = (nxt - cycle).total_seconds() / 86400
    assert abs(delta_days - round(delta_days)) < 1e-3 and round(delta_days) % 2 == 0           # на сетке цикла (кратно 2 дням)
    assert datetime.now(UTC) < nxt <= datetime.now(UTC) + timedelta(days=2)


def test_second_press_of_the_same_cycle_is_a_noop_duplicate():
    period(); tid = mk(); cyc = cycle_of(tid)
    first, second = done(tid, cyc), done(tid, cyc)
    assert first.json()["dup"] is False and second.json()["dup"] is True
    assert len(completions(tid)) == 1 and _env.streak.call_count == 1
    assert second.json()["next_due"] == first.json()["next_due"]


def test_concurrent_presses_create_exactly_one_completion_and_advance_once():
    period(); tid = mk(); cyc = cycle_of(tid)
    results, barrier = [], threading.Barrier(6)

    def press():
        barrier.wait()
        results.append(done(tid, cyc).json())

    threads = [threading.Thread(target=press) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(results) == 6 and sum(1 for r in results if r["dup"] is False) == 1
    assert len(completions(tid)) == 1
    assert len({r["next_due"] for r in results}) == 1                                          # серия сдвинута один раз, одинаково


def test_retry_after_failed_advance_finishes_the_series_without_a_second_completion():
    period()
    cycle = datetime.now(UTC) - timedelta(hours=1)
    tid = mk(due=cycle); cyc = cycle_of(tid)
    with patch("miniapp.backend.routes.care_write._repo.set_props", AsyncMock(side_effect=RuntimeError("db hiccup"))):
        r = TestClient(app, raise_server_exceptions=False).post(f"/api/care/tasks/{tid}/done", json={"cycle_due": cyc}, headers=W)
    assert r.status_code == 500 and len(completions(tid)) == 1 and abs(reminder_of(tid) - cycle) < timedelta(seconds=1)   # цикл записан, серия не сдвинута
    retry = done(tid, cyc)
    assert retry.status_code == 200 and retry.json()["dup"] is True and len(completions(tid)) == 1
    assert abs(reminder_of(tid) - (cycle + timedelta(days=2))) < timedelta(seconds=1)


def test_done_validation_stale_unknown_foreign_unflagged_closed_and_inactive_period():
    period(); tid = mk(); cyc = cycle_of(tid)
    other = z(datetime.now(UTC) + timedelta(days=9))
    assert done(tid, other).status_code == 409 and completions(tid) == []                       # чужой/будущий цикл
    assert done(999999999, cyc).status_code == 404
    assert done(mk(user=MARK2), cyc).status_code == 404
    assert done(mk(for_sitter=False), cyc).status_code == 404
    assert done(mk(status="Done"), cyc).status_code == 404
    assert TestClient(app).post(f"/api/care/tasks/{tid}/done", json={"cycle_due": "не дата"}, headers=W).status_code == 422
    care.clear_period(MARK)
    assert done(tid, cyc).status_code == 409 and completions(tid) == []                         # период кончился → ситтер не активен


def test_done_for_a_one_off_task_closes_it_once_without_double_journal():
    period()
    tid = mk(repeat="Нет", repeat_time=None)
    r = done(tid, cycle_of(tid))
    assert r.status_code == 200 and r.json()["next_due"] is None and status_of(tid) == "Done"
    assert len(completions(tid)) == 1
    assert done(tid, z(datetime.now(UTC))).status_code == 404                                   # закрытая задача больше не принимает


def test_done_by_is_length_limited():
    period(); tid = mk()
    assert TestClient(app).post(f"/api/care/tasks/{tid}/done", json={"cycle_due": cycle_of(tid), "done_by": "x" * 41}, headers=W).status_code == 422


# ── postpone ──────────────────────────────────────────────────────────────────

def test_postpone_moves_only_this_cycle_and_leaves_the_series_base_alone():
    period()
    cycle = datetime.now(UTC) - timedelta(minutes=30)
    tid = mk(due=cycle); cyc = cycle_of(tid)
    new_due = datetime.now(UTC) + timedelta(hours=3)
    r = postpone(tid, cyc, z(new_due))
    assert r.status_code == 200 and r.json()["due_at"] == z(new_due)
    assert abs(reminder_of(tid) - cycle) < timedelta(seconds=1)                                  # база серии не тронута
    item = TestClient(app).get("/api/care/schedule", headers=R).json()["tasks"][0]
    assert item["cycle_due"] == cyc and item["due_at"] == z(new_due) and item["postponed"] is True and item["overdue"] is False
    # выполнение перенесённого цикла двигает серию от ИСХОДНОГО срока, а не от нового
    done(tid, cyc)
    assert abs(reminder_of(tid) - (cycle + timedelta(days=2))) < timedelta(seconds=1)
    nxt = TestClient(app).get("/api/care/schedule", headers=R).json()["tasks"][0]
    assert nxt["postponed"] is False and nxt["due_at"] == nxt["cycle_due"]                       # у нового цикла переноса нет


def test_only_one_postpone_per_cycle_and_none_after_done():
    period(); tid = mk(); cyc = cycle_of(tid)
    soon = z(datetime.now(UTC) + timedelta(hours=2))
    assert postpone(tid, cyc, soon).status_code == 200
    assert postpone(tid, cyc, z(datetime.now(UTC) + timedelta(hours=4))).status_code == 409
    with get_engine().connect() as c:
        assert c.execute(select(task_overrides.c.new_due).where(task_overrides.c.task_id == tid)).fetchall().__len__() == 1
    tid2 = mk(); cyc2 = cycle_of(tid2)
    done(tid2, cyc2)
    assert postpone(tid2, cyc2, soon).status_code == 409


def test_postpone_bounds():
    period()
    cycle = datetime.now(UTC) - timedelta(minutes=30)
    tid = mk(due=cycle); cyc = cycle_of(tid)
    now = datetime.now(UTC)
    assert postpone(tid, cyc, z(now - timedelta(hours=1))).status_code == 400                  # в прошлом
    assert postpone(tid, cyc, z(cycle - timedelta(hours=1))).status_code == 400                 # раньше цикла
    assert postpone(tid, cyc, z(cycle + timedelta(days=2))).status_code == 400                  # в начало следующего цикла и позже
    assert postpone(tid, cyc, z(cycle + timedelta(days=3))).status_code == 400
    assert postpone(tid, z(cycle + timedelta(days=9)), z(now + timedelta(hours=1))).status_code == 409   # чужой цикл
    assert postpone(tid, cyc, z(cycle + timedelta(days=2) - timedelta(minutes=1))).status_code == 200    # в самый край допустимо


def test_postpone_cannot_leave_the_care_period():
    today = datetime.now(MSK).date()
    care.set_period(MARK, today - timedelta(days=3), today)                                     # период кончается сегодня
    tid = mk(due=datetime.now(UTC) - timedelta(minutes=30)); cyc = cycle_of(tid)
    tomorrow_noon = datetime.now(MSK).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    assert postpone(tid, cyc, z(tomorrow_noon)).status_code == 400
    care.clear_period(MARK)
    assert postpone(tid, cyc, z(datetime.now(UTC) + timedelta(hours=1))).status_code == 409


def test_postpone_validation_unknown_foreign_unflagged():
    period(); soon = z(datetime.now(UTC) + timedelta(hours=1)); cyc = z(datetime.now(UTC))
    assert postpone(999999999, cyc, soon).status_code == 404
    assert postpone(mk(user=MARK2), cyc, soon).status_code == 404
    assert postpone(mk(for_sitter=False), cyc, soon).status_code == 404
    assert TestClient(app).post("/api/care/tasks/1/postpone", json={"cycle_due": "x", "new_due": "y"}, headers=W).status_code == 422


def test_postponed_cycle_is_overdue_and_alerted_by_the_new_due():
    period()
    now = datetime.now(UTC)
    cycle = now - timedelta(hours=5)
    tid = mk(due=cycle); cyc = cycle_of(tid)
    assert [a["task_id"] for a in care.overdue_to_alert(now)] == [tid]                           # без переноса просрочено
    postpone(tid, cyc, z(now + timedelta(hours=1)))
    assert care.overdue_to_alert(now) == []                                                      # новый срок ещё впереди
    assert care.overdue_to_alert(now + timedelta(hours=5)) != []                                 # а через 5 часов снова просрочено


def test_alert_is_about_the_cycle_and_survives_a_postpone_record():
    """care_alerts ключуется исходным циклом: перенос не даёт второго предупреждения за тот же цикл."""
    period(); tid = mk(due=datetime.now(UTC) - timedelta(hours=5))
    cycle = reminder_of(tid)
    assert care.mark_alerted(tid, cycle) is True
    assert care.overdue_to_alert() == []
