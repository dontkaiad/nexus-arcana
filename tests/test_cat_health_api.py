"""tests/test_cat_health_api.py — здоровье котов, шаг 2: токен CATS_WRITE_TOKEN, маршруты /api/cat-health/*, задачи-напоминания.

Реальная БД, вымышленные коты, токены вымышленные. Напоминания проверяются по строкам tasks, планировщик подменён."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from core import cat_health as svc
from core.config import config
from core.db import get_engine
from core.repos import pg_cat_health_repo as repo
from core.repos import pg_cats_repo
from core.repos.cats_tables import cats
from miniapp.backend import care_period_auth as pgate
from miniapp.backend import care_write_auth as wgate
from miniapp.backend import cats_read_auth as cgate
from miniapp.backend import cats_write_auth as hgate
from miniapp.backend import sitter_lists_auth as rgate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK, OTHER = "test-cat-health-api", "test-cat-health-api-other"
OWNER = 700000123
HEALTH = "test-only-health-" + "a1" * 12
READ = "test-only-sitter-" + "d6" * 12
CATSREAD = "test-only-cats-" + "e5" * 12
CAREW = "test-only-write-" + "c7" * 12
PERIOD = "test-only-period-" + "b2" * 12
H = {"Authorization": f"Bearer {HEALTH}"}
TZ = 3


def local_today() -> date:
    return datetime.now(timezone(timedelta(hours=TZ))).date()


D = lambda n: local_today() + timedelta(days=n)  # noqa: E731


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k, v in (("cats_write_token", HEALTH), ("cats_write_owner_tg_id", OWNER), ("sitter_lists_token", READ), ("sitter_lists_owner_tg_id", OWNER),
                 ("cats_read_token", CATSREAD), ("cats_read_owner_tg_id", OWNER), ("care_write_token", CAREW), ("care_write_owner_tg_id", OWNER),
                 ("care_period_token", PERIOD), ("care_period_owner_tg_id", OWNER)):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    for m in (hgate, rgate, cgate, wgate, pgate):
        monkeypatch.setattr(m, "_limiter", m.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    resched, cancel = AsyncMock(), MagicMock()
    with patch("core.cat_health.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.cat_health.get_user_id", AsyncMock(return_value=MARK)), \
         patch("core.cat_health._today_tz", AsyncMock(side_effect=lambda tg: (local_today(), TZ))), \
         patch("miniapp.backend.routes.writes._reschedule_task_reminder", resched), \
         patch("miniapp.backend.routes.writes._cancel_task_jobs", cancel):
        _env.resched, _env.cancel = resched, cancel
        yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks WHERE user_id IN (:a, :b)"), {"a": MARK, "b": OTHER})
        c.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def cat():
    return pg_cats_repo.create(MARK, "Тучка")


def task(tid):
    with get_engine().connect() as c:
        return c.execute(text(
            "SELECT t.id, t.title, t.cat_id, t.user_id, t.reminder, t.deadline, t.deadline_all_day, t.repeat_time, t.note, s.code AS status, "
            "(SELECT code FROM task_category WHERE id = t.category_id) AS category, (SELECT code FROM task_repeat WHERE id = t.repeat_id) AS repeat "
            "FROM tasks t JOIN task_status s ON s.id = t.status_id WHERE t.id = :i"), {"i": tid}).first()


def n_tasks():
    with get_engine().connect() as c:
        return c.execute(text("SELECT count(*) FROM tasks WHERE user_id = :u"), {"u": MARK}).scalar()


# ── токен и изоляция маршрутов ────────────────────────────────────────────────────────────────────────────────────────

ROUTES = {
    ("GET", "/api/cat-health/cats/{cat_id}/weights"), ("POST", "/api/cat-health/cats/{cat_id}/weights"),
    ("PATCH", "/api/cat-health/weights/{weight_id}"), ("DELETE", "/api/cat-health/weights/{weight_id}"),
    ("GET", "/api/cat-health/cats/{cat_id}/vet"), ("POST", "/api/cat-health/cats/{cat_id}/vet"),
    ("PATCH", "/api/cat-health/vet/{visit_id}"), ("DELETE", "/api/cat-health/vet/{visit_id}"),
    ("GET", "/api/cat-health/cats/{cat_id}/meds"), ("POST", "/api/cat-health/cats/{cat_id}/meds"),
    ("PATCH", "/api/cat-health/meds/{med_id}"), ("DELETE", "/api/cat-health/meds/{med_id}"),
    ("GET", "/api/cat-health/upcoming"),
}


def _opened_by(hdr):
    c = TestClient(app, raise_server_exceptions=False)
    opened = set()
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api/"):
            continue
        path = r.path
        for name in getattr(r, "param_convertors", {}):
            path = path.replace("{%s}" % name, "1")
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            kw = {"json": {}} if method in ("POST", "PUT", "PATCH", "DELETE") else {}
            for g in (hgate, rgate, cgate, wgate, pgate):          # the failed-attempts window must not turn 401 into 429 half-way through the walk
                g._limiter.reset()
            with_t = c.request(method, path, headers=hdr, **kw).status_code
            without = c.request(method, path, **kw).status_code
            if with_t not in (401, 403) and without in (401, 403):
                opened.add((method, r.path))
    return opened


def test_health_token_opens_exactly_the_health_routes():
    assert _opened_by(H) == ROUTES


@pytest.mark.parametrize("tok", [READ, CATSREAD, CAREW, PERIOD])
def test_other_tokens_open_none_of_the_health_routes(tok):
    assert not {r for r in _opened_by({"Authorization": f"Bearer {tok}"}) if r[1].startswith("/api/cat-health")}


def test_every_route_in_the_prefix_is_in_the_expected_set():
    routes = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/cat-health") for m in r.methods - {"HEAD", "OPTIONS"}}
    assert routes == ROUTES                                      # a new route needs a conscious decision here


def test_no_header_wrong_token_and_initdata_are_401(client, cat):
    assert client.get("/api/cat-health/upcoming").status_code == 401
    assert client.get("/api/cat-health/upcoming", headers={"Authorization": f"Bearer {HEALTH}x"}).status_code == 401
    assert client.get("/api/cat-health/upcoming", headers={"Authorization": HEALTH}).status_code == 401
    app.dependency_overrides[current_user_id] = lambda: OWNER                     # an owner session does not get in without the token
    try:
        assert client.get("/api/cat-health/upcoming").status_code == 401
    finally:
        app.dependency_overrides.pop(current_user_id, None)
    assert client.get("/api/cat-health/upcoming", headers=H).status_code == 200


@pytest.mark.parametrize("attr,value", [("cats_write_token", "short"), ("cats_write_token", ""), ("cats_write_owner_tg_id", 0), ("cats_write_owner_tg_id", 42),
                                        ("cats_write_token", READ), ("cats_write_token", CATSREAD), ("cats_write_token", CAREW), ("cats_write_token", PERIOD)])
def test_feature_is_off_for_a_bad_configuration(client, monkeypatch, attr, value):
    monkeypatch.setattr(config, attr, value)
    for tok in (HEALTH, value if isinstance(value, str) else HEALTH):
        assert client.get("/api/cat-health/upcoming", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


@pytest.mark.parametrize("other_attr", ["sitter_lists_token", "cats_read_token", "care_write_token", "care_period_token"])
def test_a_token_equal_to_another_switches_both_off(client, monkeypatch, other_attr):
    monkeypatch.setattr(config, other_attr, HEALTH)
    assert client.get("/api/cat-health/upcoming", headers=H).status_code == 401
    gates = {"sitter_lists_token": (rgate, "/api/sitter/lists"), "cats_read_token": (cgate, "/api/cats"), "care_write_token": (wgate, "/api/care/tasks/1/done"),
             "care_period_token": (pgate, "/api/care/period/auto")}
    _, path = gates[other_attr]
    method = "GET" if path in ("/api/sitter/lists", "/api/cats") else ("POST" if "done" in path else "PUT")
    assert client.request(method, path, headers=H, json={}).status_code in (401, 403)      # and the other feature does not take this token either
    assert gates[other_attr][0]._enabled() is False                                         # the gate itself is off, not merely refused further on


def test_too_many_bad_attempts_give_429_and_the_token_is_never_logged(client, caplog):
    caplog.set_level("DEBUG")
    for _ in range(10):
        assert client.get("/api/cat-health/upcoming", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/api/cat-health/upcoming", headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert HEALTH not in caplog.text


def test_responses_are_not_cached_and_carry_no_user_id(client, cat):
    r = client.post(f"/api/cat-health/cats/{cat}/weights", json={"grams": 4200}, headers=H)
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "user_id" not in r.json()["weight"] and MARK not in r.text


# ── вес ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_weight_crud_same_day_replaces_and_flag(client, cat):
    a = client.post(f"/api/cat-health/cats/{cat}/weights", json={"grams": 4200, "weighed_on": D(-7).isoformat()}, headers=H).json()["weight"]
    b = client.post(f"/api/cat-health/cats/{cat}/weights", json={"grams": 4300, "note": "утром"}, headers=H).json()["weight"]
    assert b["weighed_on"] == local_today().isoformat() and b["sitter_visible"] is False
    again = client.post(f"/api/cat-health/cats/{cat}/weights", json={"grams": 4350}, headers=H).json()["weight"]
    assert again["id"] == b["id"] and again["grams"] == 4350
    rows = client.get(f"/api/cat-health/cats/{cat}/weights", headers=H).json()["weights"]
    assert [w["grams"] for w in rows] == [4350, 4200]
    assert client.patch(f"/api/cat-health/weights/{b['id']}", json={"sitter_visible": True}, headers=H).json()["weight"]["sitter_visible"] is True
    assert client.delete(f"/api/cat-health/weights/{a['id']}", headers=H).status_code == 200
    assert client.delete(f"/api/cat-health/weights/{a['id']}", headers=H).status_code == 404
    assert len(client.get(f"/api/cat-health/cats/{cat}/weights", headers=H).json()["weights"]) == 1


@pytest.mark.parametrize("body", [{"grams": 99}, {"grams": 30001}, {"grams": "много"}, {"grams": 4200.5}, {}, {"grams": 4200, "note": "я" * 301}, {"grams": 4200, "weighed_on": "вчера"}])
def test_weight_validation_is_422(client, cat, body):
    assert client.post(f"/api/cat-health/cats/{cat}/weights", json=body, headers=H).status_code == 422
    assert client.get(f"/api/cat-health/cats/{cat}/weights", headers=H).json()["weights"] == []


def test_foreign_removed_and_unknown_cats_are_404_everywhere(client, cat):
    theirs = pg_cats_repo.create(OTHER, "Чужой")
    gone = pg_cats_repo.create(MARK, "Убранный")
    pg_cats_repo.update_cat(MARK, gone, {"status": "rejected"})
    for cid in (theirs, gone, 987654321):
        for path, body in (("weights", {"grams": 4200}), ("vet", {"title": "осмотр", "visit_on": D(3).isoformat()}), ("meds", {"name": "капли"})):
            assert client.post(f"/api/cat-health/cats/{cid}/{path}", json=body, headers=H).status_code == 404, (cid, path)
            assert client.get(f"/api/cat-health/cats/{cid}/{path}", headers=H).status_code == 404, (cid, path)
    assert n_tasks() == 0


def test_foreign_rows_cannot_be_read_changed_or_deleted(client, cat):
    theirs = pg_cats_repo.create(OTHER, "Чужой")
    w = repo.add_weight(OTHER, theirs, D(0), 5000)
    v = repo.add_visit(OTHER, theirs, "осмотр", D(5))
    m = repo.add_med(OTHER, theirs, "капли", D(0), days=3)
    for method, path, body in (("PATCH", f"/api/cat-health/weights/{w['id']}", {"sitter_visible": True}), ("DELETE", f"/api/cat-health/weights/{w['id']}", None),
                               ("PATCH", f"/api/cat-health/vet/{v['id']}", {"note": "x"}), ("DELETE", f"/api/cat-health/vet/{v['id']}", None),
                               ("PATCH", f"/api/cat-health/meds/{m['id']}", {"name": "x"}), ("DELETE", f"/api/cat-health/meds/{m['id']}", None)):
        assert client.request(method, path, json=body, headers=H).status_code == 404, path
    assert repo.get_visit(OTHER, v["id"])["note"] is None and repo.get_med(OTHER, m["id"])["name"] == "капли" and len(repo.list_weights(OTHER, theirs)) == 1


# ── визиты и задачи ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_planned_visit_creates_a_reminder_task(client, cat):
    r = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Осмотр", "visit_on": D(10).isoformat(), "kind": "vaccine", "clinic": "Клиника у дома"}, headers=H)
    v = r.json()["visit"]
    assert r.status_code == 200 and v["task_linked"] is True and v["status"] == "planned" and v["sitter_visible"] is False and "user_id" not in v
    t = task(v["task_id"])
    assert t.title == "💉 Тучка: Осмотр" and t.cat_id == cat and t.user_id == MARK and t.category == "🐾 Коты" and t.status == "Not started"
    assert t.reminder == datetime(D(9).year, D(9).month, D(9).day, 9, 0, tzinfo=timezone(timedelta(hours=TZ)))        # the day before, 09:00 local
    assert t.deadline.astimezone(timezone(timedelta(hours=TZ))).date() == D(10) and t.deadline_all_day is True
    _env.resched.assert_awaited_once()
    assert repo.get_visit(MARK, v["id"])["task_id"] == v["task_id"]


def test_visit_done_with_next_due_moves_the_task_to_the_next_term(client, cat):
    v = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Прививка", "visit_on": D(10).isoformat(), "kind": "vaccine"}, headers=H).json()["visit"]
    old = v["task_id"]
    r = client.patch(f"/api/cat-health/vet/{v['id']}", json={"status": "done", "next_due": D(375).isoformat()}, headers=H).json()["visit"]
    assert task(old).status == "Done"
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM task_completions WHERE task_id = :t"), {"t": old}).scalar() == 1
    new = task(r["task_id"])
    assert r["task_id"] != old and new.title == "💉 Тучка: Прививка (следующий срок)" and new.deadline.astimezone(timezone(timedelta(hours=TZ))).date() == D(375)
    assert new.status == "Not started" and new.cat_id == cat


def test_visit_done_without_next_due_closes_the_task_and_creates_none(client, cat):
    v = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Осмотр", "visit_on": D(10).isoformat()}, headers=H).json()["visit"]
    r = client.patch(f"/api/cat-health/vet/{v['id']}", json={"status": "done"}, headers=H).json()["visit"]
    assert task(v["task_id"]).status == "Done" and r["task_id"] is None and r["task_linked"] is False and n_tasks() == 1


def test_cancel_and_delete_archive_the_task_and_cancel_jobs(client, cat):
    a = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Один", "visit_on": D(10).isoformat()}, headers=H).json()["visit"]
    b = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Два", "visit_on": D(11).isoformat()}, headers=H).json()["visit"]
    client.patch(f"/api/cat-health/vet/{a['id']}", json={"status": "cancelled"}, headers=H)
    assert task(a["task_id"]).status == "Archived" and repo.get_visit(MARK, a["id"])["task_id"] is None
    assert client.delete(f"/api/cat-health/vet/{b['id']}", headers=H).status_code == 200
    assert task(b["task_id"]).status == "Archived" and repo.get_visit(MARK, b["id"]) is None
    assert {c.args[0] for c in _env.cancel.call_args_list} >= {str(a["task_id"]), str(b["task_id"])}


def test_moving_the_date_recreates_the_task_but_a_note_does_not(client, cat):
    v = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Осмотр", "visit_on": D(10).isoformat()}, headers=H).json()["visit"]
    same = client.patch(f"/api/cat-health/vet/{v['id']}", json={"note": "взять переноску", "sitter_visible": True}, headers=H).json()["visit"]
    assert same["task_id"] == v["task_id"] and n_tasks() == 1 and same["note"] == "взять переноску" and same["sitter_visible"] is True
    moved = client.patch(f"/api/cat-health/vet/{v['id']}", json={"visit_on": D(20).isoformat()}, headers=H).json()["visit"]
    assert moved["task_id"] != v["task_id"] and task(v["task_id"]).status == "Archived"
    assert task(moved["task_id"]).deadline.astimezone(timezone(timedelta(hours=TZ))).date() == D(20)


def test_visit_validation_patch_nulls_and_unknown_fields(client, cat):
    base = {"title": "Осмотр", "visit_on": D(10).isoformat()}
    for bad in ({"title": ""}, {"title": "я" * 121}, {"kind": "surgery"}, {"status": "maybe"}, {"visit_on": "завтра"}, {"next_due": D(5).isoformat()}, {"clinic": "я" * 121}):
        assert client.post(f"/api/cat-health/cats/{cat}/vet", json={**base, **bad}, headers=H).status_code == 422, bad
    assert n_tasks() == 0
    v = client.post(f"/api/cat-health/cats/{cat}/vet", json={**base, "next_due": D(300).isoformat(), "clinic": "К"}, headers=H).json()["visit"]
    p = client.patch(f"/api/cat-health/vet/{v['id']}", json={"next_due": None, "clinic": None, "user_id": "x", "cat_id": 1, "task_id": 5}, headers=H)
    assert p.status_code == 200 and p.json()["visit"]["next_due"] is None and p.json()["visit"]["clinic"] is None
    assert repo.get_visit(MARK, v["id"])["cat_id"] == cat and repo.get_visit(MARK, v["id"])["user_id"] == MARK
    for k in ("title", "status", "visit_on", "kind"):
        assert client.patch(f"/api/cat-health/vet/{v['id']}", json={k: None}, headers=H).status_code == 422
    assert client.patch(f"/api/cat-health/vet/{v['id']}", json={"visit_on": D(400).isoformat(), "next_due": D(300).isoformat()}, headers=H).status_code == 422
    assert client.patch("/api/cat-health/vet/987654321", json={"note": "x"}, headers=H).status_code == 404


def test_a_failed_task_does_not_lose_the_record(client, cat):
    with patch.object(svc._tasks, "create", AsyncMock(side_effect=RuntimeError("db down"))):
        r = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Осмотр", "visit_on": D(10).isoformat()}, headers=H)
    v = r.json()["visit"]
    assert r.status_code == 200 and v["task_id"] is None and v["task_linked"] is False and repo.get_visit(MARK, v["id"]) is not None
    p = client.patch(f"/api/cat-health/vet/{v['id']}", json={"note": "повтор"}, headers=H).json()["visit"]          # the next edit tries again
    assert p["task_linked"] is True and task(p["task_id"]) is not None


def test_overdue_visit_still_gets_a_task_without_a_reminder(client, cat):
    v = client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Давно", "visit_on": D(-3).isoformat()}, headers=H).json()["visit"]
    t = task(v["task_id"])
    assert t is not None and t.reminder is None and t.deadline.astimezone(timezone(timedelta(hours=TZ))).date() == D(-3)
    _env.resched.assert_not_awaited()


# ── курсы лекарств ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_med_creates_a_daily_reminder_task(client, cat):
    r = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли", "dose": "по 2 капли", "days": 10, "times_per_day": 2}, headers=H)
    m = r.json()["med"]
    assert r.status_code == 200 and m["starts_on"] == local_today().isoformat() and m["ends_on"] == D(9).isoformat() and m["task_linked"] is True
    t = task(m["task_id"])
    assert t.title == "💊 Тучка: капли — по 2 капли (2 р/день)" and t.repeat == "Ежедневно" and t.repeat_time == "09:00" and t.cat_id == cat and t.category == "🐾 Коты"
    assert t.reminder.astimezone(timezone(timedelta(hours=TZ))).hour == 9 and t.reminder > datetime.now(timezone.utc) and t.note == f"курс до {D(9):%d.%m}"
    _env.resched.assert_awaited_once()


def test_future_start_sets_the_first_reminder_on_the_start_day(client, cat):
    m = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли", "starts_on": D(5).isoformat(), "days": 3}, headers=H).json()["med"]
    assert task(m["task_id"]).reminder.astimezone(timezone(timedelta(hours=TZ))).date() == D(5)


def test_stopping_changing_and_deleting_a_course_closes_or_replaces_the_task(client, cat):
    m = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли", "days": 10}, headers=H).json()["med"]
    same = client.patch(f"/api/cat-health/meds/{m['id']}", json={"note": "после еды", "sitter_hidden": True}, headers=H).json()["med"]
    assert same["task_id"] == m["task_id"] and same["sitter_hidden"] is True and n_tasks() == 1
    longer = client.patch(f"/api/cat-health/meds/{m['id']}", json={"days": 20}, headers=H).json()["med"]
    assert longer["task_id"] != m["task_id"] and task(m["task_id"]).status == "Archived" and longer["ends_on"] == D(19).isoformat()
    stopped = client.patch(f"/api/cat-health/meds/{m['id']}", json={"status": "done"}, headers=H).json()["med"]
    assert task(longer["task_id"]).status == "Done" and stopped["task_id"] is None and stopped["task_linked"] is False
    n = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "таблетки", "days": 5}, headers=H).json()["med"]
    assert client.delete(f"/api/cat-health/meds/{n['id']}", headers=H).status_code == 200 and task(n["task_id"]).status == "Archived"
    assert client.delete(f"/api/cat-health/meds/{n['id']}", headers=H).status_code == 404


def test_a_finished_course_is_closed_when_listed_and_its_task_completed(client, cat):
    m = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли", "days": 3}, headers=H).json()["med"]
    with get_engine().begin() as c:                                                      # the course ended two days ago
        c.execute(text("UPDATE cat_medications SET starts_on = :s, ends_on = :e WHERE id = :i"), {"s": D(-6), "e": D(-2), "i": m["id"]})
    rows = client.get(f"/api/cat-health/cats/{cat}/meds", headers=H).json()["meds"]
    assert rows[0]["status"] == "done" and rows[0]["task_id"] is None and task(m["task_id"]).status == "Done"
    assert client.get(f"/api/cat-health/cats/{cat}/meds", headers=H).json()["meds"][0]["status"] == "done"      # repeated: no change, no error
    assert client.get(f"/api/cat-health/cats/{cat}/meds?status=active", headers=H).json()["meds"] == []


def test_med_validation(client, cat):
    for bad in ({"name": ""}, {"name": "я" * 81}, {"days": 0}, {"days": 366}, {"times_per_day": 0}, {"times_per_day": 5}, {"dose": "я" * 81}, {"starts_on": "завтра"}):
        assert client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли", **bad}, headers=H).status_code == 422, bad
    assert n_tasks() == 0
    m = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли"}, headers=H).json()["med"]
    for k in ("name", "status", "starts_on", "times_per_day", "sitter_hidden"):
        assert client.patch(f"/api/cat-health/meds/{m['id']}", json={k: None}, headers=H).status_code == 422
    assert client.patch(f"/api/cat-health/meds/{m['id']}", json={"status": "weird"}, headers=H).status_code == 422
    p = client.patch(f"/api/cat-health/meds/{m['id']}", json={"cat_id": 1, "user_id": "x", "ends_on": D(99).isoformat()}, headers=H).json()["med"]
    assert p["cat_id"] == cat and p["ends_on"] is None


def test_upcoming_summary(client, cat):
    client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Осмотр", "visit_on": D(4).isoformat()}, headers=H)
    client.post(f"/api/cat-health/cats/{cat}/vet", json={"title": "Давно", "visit_on": D(-3).isoformat()}, headers=H)
    client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "капли", "days": 6}, headers=H)
    b = client.get("/api/cat-health/upcoming", headers=H).json()
    assert b["today"] == local_today().isoformat()
    assert [(e["type"], e["overdue"]) for e in b["events"]] == [("vet", True), ("vet", False), ("med_end", False)]
    assert [m["name"] for m in b["current_meds"]] == ["капли"] and all("user_id" not in e for e in b["events"])
    assert client.get("/api/cat-health/upcoming?days=0", headers=H).status_code == 422


# ── вычисление напоминаний ────────────────────────────────────────────────────────────────────────────────────────────

def _utc(y, m, d, h):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def test_visit_reminder_choices():
    day = date(2026, 12, 10)
    assert svc.visit_reminder(day, 3, _utc(2026, 12, 1, 0)) == "2026-12-09T09:00"                      # plenty of time: the day before
    assert svc.visit_reminder(day, 3, _utc(2026, 12, 9, 5)) == "2026-12-09T09:00"                      # 08:00 local the day before: still ahead
    assert svc.visit_reminder(day, 3, _utc(2026, 12, 9, 6)) == "2026-12-10T09:00"                      # 09:00 local the day before has passed: the day itself
    assert svc.visit_reminder(day, 3, _utc(2026, 12, 10, 6)) is None                                   # both have passed
    assert svc.visit_reminder(day, -5, _utc(2026, 12, 9, 13)) == "2026-12-09T09:00"                    # another time zone


def test_med_reminder_choices():
    d = date(2026, 12, 10)
    assert svc.med_reminder(d, 3, _utc(2026, 12, 10, 5)) == "2026-12-10T09:00"
    assert svc.med_reminder(d, 3, _utc(2026, 12, 10, 6)) == "2026-12-11T09:00"                         # 09:00 local today has passed: tomorrow


def test_weights_limit_is_bounded(client, cat):
    for bad in (0, -1, 1001):
        assert client.get(f"/api/cat-health/cats/{cat}/weights?limit={bad}", headers=H).status_code == 422
    assert client.get(f"/api/cat-health/cats/{cat}/weights?limit=1000", headers=H).status_code == 200


def test_a_one_day_course_whose_only_reminder_would_fall_after_its_end_gets_no_task(client, cat):
    with patch("core.cat_health.med_reminder", return_value=f"{D(2).isoformat()}T09:00"):
        m = client.post(f"/api/cat-health/cats/{cat}/meds", json={"name": "укол", "days": 1}, headers=H).json()["med"]
    assert m["task_id"] is None and m["task_linked"] is False and n_tasks() == 0
