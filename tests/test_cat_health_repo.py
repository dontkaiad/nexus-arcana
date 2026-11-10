"""tests/test_cat_health_repo.py — здоровье котов, шаг 1: миграция и репозиторий (вес, визиты / прививки, курсы лекарств).

Реальная БД (DATABASE_URL), вымышленные коты, строки помечены MARK. Денег в схеме нет — проверяется отдельно."""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.exc import IntegrityError

from core.db import get_engine
from core.repos import pg_cat_health_repo as repo
from core.repos import pg_cats_repo
from core.repos.cats_tables import cats

REPO = Path(__file__).resolve().parent.parent
MARK, OTHER = "test-cat-health", "test-cat-health-other"
TODAY = date(2026, 12, 1)
D = lambda n: TODAY + timedelta(days=n)  # noqa: E731


@pytest.fixture(autouse=True)
def _clean():
    yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks WHERE user_id IN (:a, :b)"), {"a": MARK, "b": OTHER})
        c.execute(delete(cats).where(cats.c.user_id.in_([MARK, OTHER])))


@pytest.fixture
def cat():
    return pg_cats_repo.create(MARK, "Тучка")


def _task(user=MARK) -> int:
    with get_engine().begin() as c:
        sid = c.execute(text("SELECT id FROM task_status ORDER BY id LIMIT 1")).scalar()
        return c.execute(text("INSERT INTO tasks (title, status_id, user_id) VALUES ('тест', :s, :u) RETURNING id"), {"s": sid, "u": user}).scalar()


# ── схема ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_tables_have_no_money_columns_and_hide_from_sitter_by_default():
    insp = inspect(get_engine())
    for t in ("cat_weights", "cat_vet_visits", "cat_medications"):
        cols = {c["name"] for c in insp.get_columns(t)}
        assert not cols & {"price", "cost", "amount", "sum", "money", "rub"}, t
    assert {c["name"]: c for c in insp.get_columns("cat_weights")}["sitter_visible"]["default"] == "false"
    assert {c["name"]: c for c in insp.get_columns("cat_vet_visits")}["sitter_visible"]["default"] == "false"
    assert {c["name"]: c for c in insp.get_columns("cat_medications")}["sitter_hidden"]["default"] == "false"


def test_cascade_with_the_cat_and_task_link_set_null(cat):
    repo.add_weight(MARK, cat, TODAY, 4200)
    tid = _task()
    v = repo.add_visit(MARK, cat, "осмотр", D(3))
    repo.set_visit_task(MARK, v["id"], tid)
    m = repo.add_med(MARK, cat, "капли", D(0), days=5)
    repo.set_med_task(MARK, m["id"], tid)
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks WHERE id = :t"), {"t": tid})                     # the task goes away: the records stay
    assert repo.get_visit(MARK, v["id"])["task_id"] is None and repo.get_med(MARK, m["id"])["task_id"] is None
    with get_engine().begin() as c:
        c.execute(delete(cats).where(cats.c.id == cat))                                    # the cat goes away: its health goes with it
        n = sum(c.execute(text(f"SELECT count(*) FROM {t} WHERE cat_id = :c"), {"c": cat}).scalar() for t in ("cat_weights", "cat_vet_visits", "cat_medications"))
    assert n == 0


def test_migration_up_down_reversible():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = next((REPO / "alembic" / "versions").glob("*_cat_health.py"))
    spec = importlib.util.spec_from_file_location("_mig_cat_health", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    with get_engine().connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                mig.downgrade()
                names = inspect(conn).get_table_names()
                assert not {"cat_weights", "cat_vet_visits", "cat_medications"} & set(names) and "cats" in names and "tasks" in names
                mig.upgrade()
                assert {"cat_weights", "cat_vet_visits", "cat_medications"} <= set(inspect(conn).get_table_names())
                # what the migration itself builds: hidden-by-default flags, one weighing per day, cascade with the cat
                cid = conn.execute(text("INSERT INTO cats (user_id, name) VALUES (:u, 'Миграция') RETURNING id"), {"u": MARK}).scalar()
                conn.execute(text("INSERT INTO cat_weights (cat_id, user_id, weighed_on, grams) VALUES (:c, :u, '2026-12-01', 4000)"), {"c": cid, "u": MARK})
                conn.execute(text("INSERT INTO cat_vet_visits (cat_id, user_id, title, visit_on) VALUES (:c, :u, 'x', '2026-12-01')"), {"c": cid, "u": MARK})
                conn.execute(text("INSERT INTO cat_medications (cat_id, user_id, name, starts_on) VALUES (:c, :u, 'x', '2026-12-01')"), {"c": cid, "u": MARK})
                flags = conn.execute(text("SELECT (SELECT sitter_visible FROM cat_weights), (SELECT sitter_visible FROM cat_vet_visits), (SELECT sitter_hidden FROM cat_medications)")).one()
                assert tuple(flags) == (False, False, False)
                nested = conn.begin_nested()
                with pytest.raises(IntegrityError):
                    conn.execute(text("INSERT INTO cat_weights (cat_id, user_id, weighed_on, grams) VALUES (:c, :u, '2026-12-01', 4100)"), {"c": cid, "u": MARK})
                nested.rollback()
                conn.execute(text("DELETE FROM cats WHERE id = :c"), {"c": cid})
                assert sum(conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in ("cat_weights", "cat_vet_visits", "cat_medications")) == 0
        finally:
            trans.rollback()


# ── вес ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_weight_add_list_and_same_day_replaces_keeping_the_flag(cat):
    a = repo.add_weight(MARK, cat, D(-7), 4100, "после обеда")
    b = repo.add_weight(MARK, cat, D(0), 4200)
    assert a["sitter_visible"] is False and a["note"] == "после обеда"
    repo.set_weight_visible(MARK, b["id"], True)
    c = repo.add_weight(MARK, cat, D(0), 4250, "утром")
    assert c["id"] == b["id"] and c["grams"] == 4250 and c["note"] == "утром" and c["sitter_visible"] is True
    rows = repo.list_weights(MARK, cat)
    assert [r["grams"] for r in rows] == [4250, 4100]                                       # newest first, one row per day


@pytest.mark.parametrize("grams,ok", [(99, False), (100, True), (30000, True), (30001, False), (-5, False), (0, False), (True, False), ("4200", False), (4200.5, False)])
def test_weight_bounds_and_types(cat, grams, ok):
    if ok:
        assert repo.add_weight(MARK, cat, TODAY, grams)["grams"] == grams
    else:
        with pytest.raises(ValueError):
            repo.add_weight(MARK, cat, TODAY, grams)


def test_weight_note_is_trimmed_and_limited(cat):
    assert repo.add_weight(MARK, cat, TODAY, 4000, "  много   пробелов ")["note"] == "много пробелов"
    assert repo.add_weight(MARK, cat, D(1), 4000, "   ")["note"] is None
    with pytest.raises(ValueError):
        repo.add_weight(MARK, cat, D(2), 4000, "я" * 301)


def test_weight_is_owner_scoped(cat):
    w = repo.add_weight(MARK, cat, TODAY, 4200)
    with pytest.raises(repo.NotFound):
        repo.add_weight(OTHER, cat, TODAY, 5000)
    with pytest.raises(repo.NotFound):
        repo.list_weights(OTHER, cat)
    assert repo.set_weight_visible(OTHER, w["id"], True) is None and repo.delete_weight(OTHER, w["id"]) is False
    assert repo.list_weights(MARK, cat)[0]["sitter_visible"] is False
    assert repo.delete_weight(MARK, w["id"]) is True and repo.list_weights(MARK, cat) == []


def test_rejected_or_unknown_cat_is_not_found(cat):
    pg_cats_repo.update_cat(MARK, cat, {"status": "rejected"})
    with pytest.raises(repo.NotFound):
        repo.add_weight(MARK, cat, TODAY, 4200)
    with pytest.raises(repo.NotFound):
        repo.add_visit(MARK, 987654321, "осмотр", TODAY)


# ── визиты / прививки ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_visit_defaults_and_trimming(cat):
    v = repo.add_visit(MARK, cat, "  Осмотр   перед отъездом ", D(10), clinic=" Клиника  у дома ", note="взять переноску")
    assert v["title"] == "Осмотр перед отъездом" and v["clinic"] == "Клиника у дома" and v["kind"] == "visit"
    assert v["status"] == "planned" and v["sitter_visible"] is False and v["task_id"] is None and v["next_due"] is None


@pytest.mark.parametrize("kw", [{"title": ""}, {"title": "   "}, {"title": "я" * 121}, {"kind": "surgery"}, {"status": "maybe"}, {"next_due": D(-1)}])
def test_visit_validation(cat, kw):
    args = {"title": "осмотр", "kind": "visit", "status": "planned", "next_due": None}
    args.update(kw)
    with pytest.raises(ValueError):
        repo.add_visit(MARK, cat, args["title"], D(0), kind=args["kind"], status=args["status"], next_due=args["next_due"])


def test_visit_update_whitelist_and_consistency(cat):
    v = repo.add_visit(MARK, cat, "прививка", D(5), kind="vaccine", next_due=D(370))
    u = repo.update_visit(MARK, v["id"], {"status": "done", "note": "всё хорошо", "user_id": "x", "cat_id": 1, "task_id": 7, "id": 99})
    assert u["status"] == "done" and u["note"] == "всё хорошо" and u["user_id"] == MARK and u["cat_id"] == cat and u["task_id"] is None and u["id"] == v["id"]
    with pytest.raises(ValueError):
        repo.update_visit(MARK, v["id"], {"visit_on": D(400)})                              # the next due may not precede the visit
    assert repo.get_visit(MARK, v["id"])["visit_on"] == D(5)
    with pytest.raises(ValueError):
        repo.update_visit(MARK, v["id"], {"kind": "nope"})
    assert repo.update_visit(MARK, 987654321, {"note": "x"}) is None


def test_visit_owner_scoping_list_filter_and_delete(cat):
    a = repo.add_visit(MARK, cat, "осмотр", D(3))
    b = repo.add_visit(MARK, cat, "прививка", D(-30), kind="vaccine", status="done")
    assert [r["id"] for r in repo.list_visits(MARK, cat)] == [a["id"], b["id"]]
    assert [r["id"] for r in repo.list_visits(MARK, cat, status="done")] == [b["id"]]
    assert repo.get_visit(OTHER, a["id"]) is None and repo.update_visit(OTHER, a["id"], {"note": "x"}) is None and repo.delete_visit(OTHER, a["id"]) is None
    with pytest.raises(repo.NotFound):
        repo.list_visits(OTHER, cat)
    gone = repo.delete_visit(MARK, a["id"])
    assert gone["id"] == a["id"] and repo.get_visit(MARK, a["id"]) is None


def test_visit_db_constraints_hold_without_the_python_checks(cat):
    with get_engine().connect() as c:
        for sql in ("INSERT INTO cat_vet_visits (cat_id, user_id, title, visit_on, kind) VALUES (:c, :u, 'x', '2026-12-01', 'bad')",
                    "INSERT INTO cat_vet_visits (cat_id, user_id, title, visit_on, status) VALUES (:c, :u, 'x', '2026-12-01', 'bad')",
                    "INSERT INTO cat_vet_visits (cat_id, user_id, title, visit_on, next_due) VALUES (:c, :u, 'x', '2026-12-01', '2026-11-01')",
                    "INSERT INTO cat_weights (cat_id, user_id, weighed_on, grams) VALUES (:c, :u, '2026-12-01', 50)",
                    "INSERT INTO cat_medications (cat_id, user_id, name, starts_on, times_per_day) VALUES (:c, :u, 'x', '2026-12-01', 9)",
                    "INSERT INTO cat_medications (cat_id, user_id, name, starts_on, ends_on) VALUES (:c, :u, 'x', '2026-12-05', '2026-12-01')"):
            with pytest.raises(IntegrityError):
                c.execute(text(sql), {"c": cat, "u": MARK})
            c.rollback()


# ── курсы лекарств ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_med_end_is_computed_inclusive(cat):
    m = repo.add_med(MARK, cat, "капли", D(14), days=10, dose="по 2 капли", times_per_day=2)
    assert m["ends_on"] == D(23) and m["status"] == "active" and m["sitter_hidden"] is False and m["times_per_day"] == 2
    assert repo.add_med(MARK, cat, "постоянные", D(0))["ends_on"] is None


@pytest.mark.parametrize("kw", [{"name": " "}, {"name": "я" * 81}, {"days": 0}, {"days": 366}, {"days": True}, {"times_per_day": 0}, {"times_per_day": 5}, {"dose": "я" * 81}])
def test_med_validation(cat, kw):
    args = {"name": "капли", "days": 5, "times_per_day": 1, "dose": None}
    args.update(kw)
    with pytest.raises(ValueError):
        repo.add_med(MARK, cat, args["name"], D(0), days=args["days"], times_per_day=args["times_per_day"], dose=args["dose"])


def test_med_update_recomputes_end_and_ignores_foreign_fields(cat):
    m = repo.add_med(MARK, cat, "капли", D(0), days=5)
    u = repo.update_med(MARK, m["id"], {"starts_on": D(10), "days": 7, "cat_id": 1, "user_id": "x", "ends_on": D(999), "task_id": 3})
    assert u["ends_on"] == D(16) and u["cat_id"] == cat and u["user_id"] == MARK and u["task_id"] is None
    assert repo.update_med(MARK, m["id"], {"days": None})["ends_on"] is None
    with pytest.raises(ValueError):
        repo.update_med(MARK, m["id"], {"status": "weird"})
    assert repo.update_med(OTHER, m["id"], {"name": "x"}) is None and repo.get_med(OTHER, m["id"]) is None and repo.delete_med(OTHER, m["id"]) is None


def test_current_meds_only_active_started_and_not_ended(cat):
    ok = repo.add_med(MARK, cat, "идёт", D(-2), days=5)                                     # D-2 .. D+2
    last_day = repo.add_med(MARK, cat, "последний день", D(-4), days=5)                     # ends today: still current
    repo.add_med(MARK, cat, "не начался", D(1), days=5)
    repo.add_med(MARK, cat, "закончился", D(-10), days=5)
    done = repo.add_med(MARK, cat, "остановлен", D(-1), days=9)
    repo.update_med(MARK, done["id"], {"status": "done"})
    gone = repo.add_med(MARK, cat, "отменён", D(-1))
    repo.update_med(MARK, gone["id"], {"status": "cancelled"})
    forever = repo.add_med(MARK, cat, "постоянно", D(-30))
    assert {ok["id"], last_day["id"], forever["id"]} == {m["id"] for m in repo.current_meds(MARK, TODAY)}


def test_current_meds_sitter_view_skips_hidden_cat_filter_and_scope(cat):
    other_cat = pg_cats_repo.create(MARK, "Пончик")
    a = repo.add_med(MARK, cat, "видно", D(0), days=3)
    b = repo.add_med(MARK, cat, "скрыто", D(0), days=3, sitter_hidden=True)
    c = repo.add_med(MARK, other_cat, "другому коту", D(0), days=3)
    assert {m["id"] for m in repo.current_meds(MARK, TODAY)} == {a["id"], b["id"], c["id"]}
    assert {m["id"] for m in repo.current_meds(MARK, TODAY, for_sitter=True)} == {a["id"], c["id"]}
    assert {m["id"] for m in repo.current_meds(MARK, TODAY, cat, for_sitter=True)} == {a["id"]}
    assert repo.current_meds(OTHER, TODAY) == []
    pg_cats_repo.update_cat(MARK, other_cat, {"status": "rejected"})
    assert {m["id"] for m in repo.current_meds(MARK, TODAY)} == {a["id"], b["id"]}          # a removed cat's course is not shown


def test_expire_courses_is_idempotent_and_returns_closed_rows(cat):
    old = repo.add_med(MARK, cat, "старый", D(-10), days=5)
    cur = repo.add_med(MARK, cat, "идёт", D(-1), days=5)
    forever = repo.add_med(MARK, cat, "постоянно", D(-30))
    ends_today = repo.add_med(MARK, cat, "кончается сегодня", D(-4), days=5)                  # last day is today: not yet expired
    other_cat = pg_cats_repo.create(OTHER, "Чужой")
    theirs = repo.add_med(OTHER, other_cat, "чужой старый", D(-10), days=5)
    closed = repo.expire_courses(MARK, TODAY)
    assert [m["id"] for m in closed] == [old["id"]] and closed[0]["status"] == "done"
    assert repo.expire_courses(MARK, TODAY) == []                                            # repeated: nothing more to close
    assert repo.get_med(MARK, cur["id"])["status"] == "active" and repo.get_med(MARK, forever["id"])["status"] == "active"
    assert repo.get_med(MARK, ends_today["id"])["status"] == "active" and repo.get_med(OTHER, theirs["id"])["status"] == "active"   # not touched
    assert [m["id"] for m in repo.expire_courses(OTHER, TODAY)] == [theirs["id"]] and repo.expire_courses(OTHER, TODAY) == []


# ── ближайшие события ────────────────────────────────────────────────────────────────────────────────────────────────

def test_upcoming_lists_overdue_planned_next_due_and_course_end_sorted(cat):
    late = repo.add_visit(MARK, cat, "прошёл мимо", D(-3))                                  # still planned, the date has passed
    soon = repo.add_visit(MARK, cat, "осмотр", D(4))
    far = repo.add_visit(MARK, cat, "далеко", D(200))
    vac = repo.add_visit(MARK, cat, "прививка", D(-300), kind="vaccine", status="done", next_due=D(20))
    cancelled = repo.add_visit(MARK, cat, "отменён", D(2), status="cancelled")
    med = repo.add_med(MARK, cat, "капли", D(0), days=11)                                  # ends D+10
    forever = repo.add_med(MARK, cat, "постоянно", D(0))
    ev = repo.upcoming(MARK, TODAY, 60)
    assert [(e["type"], e["ref_id"]) for e in ev] == [("vet", late["id"]), ("vet", soon["id"]), ("med_end", med["id"]), ("vet_next", vac["id"])]
    assert ev[0]["overdue"] is True and ev[1]["overdue"] is False and ev[2]["date"] == D(10)
    ids = {e["ref_id"] for e in ev}
    assert far["id"] not in ids and cancelled["id"] not in ids and forever["id"] not in ids
    assert repo.upcoming(OTHER, TODAY) == []


def test_upcoming_does_not_repeat_next_due_when_the_next_visit_is_already_planned(cat):
    vac = repo.add_visit(MARK, cat, "прививка", D(-300), kind="vaccine", status="done", next_due=D(20))
    repo.add_visit(MARK, cat, "прививка", D(21), kind="vaccine")                            # the next one is already booked
    assert [e["type"] for e in repo.upcoming(MARK, TODAY)] == ["vet"]
    assert vac["id"] not in {e["ref_id"] for e in repo.upcoming(MARK, TODAY) if e["type"] == "vet_next"}
