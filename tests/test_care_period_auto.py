"""tests/test_care_period_auto.py — период ухода из дат доступа ситтеров (cats → Nexus): отдельный токен CARE_PERIOD_TOKEN,
source manual|auto, ручной период как переопределение, изоляция четырёх токенов. Реальная БД, вымышленные данные, токены вымышленные."""
from __future__ import annotations

import importlib.util
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, text

from core import care
from core.config import config
from core.db import get_engine
from miniapp.backend import care_period_auth as pgate
from miniapp.backend import care_write_auth as wgate
from miniapp.backend import cats_read_auth as cats_gate
from miniapp.backend import sitter_lists_auth as rgate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from nexus.repos.tasks_tables import care_periods

REPO = Path(__file__).resolve().parent.parent
MARK = "test-care-period-auto"
OWNER = 700000123
PERIOD = "test-only-period-" + "a9" * 12
WRITE = "test-only-write-" + "c7" * 12
READ = "test-only-sitter-" + "d6" * 12
CATS = "test-only-cats-" + "e5" * 12
P, W, R, C = ({"Authorization": f"Bearer {t}"} for t in (PERIOD, WRITE, READ, CATS))
D = date


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "care_period_token", PERIOD)
    monkeypatch.setattr(config, "care_period_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "care_write_token", WRITE)
    monkeypatch.setattr(config, "care_write_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "sitter_lists_token", READ)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "cats_read_token", CATS)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    for m in (pgate, wgate, rgate, cats_gate):
        monkeypatch.setattr(m, "_limiter", m.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.care_period_auto.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.care.get_user_id", AsyncMock(return_value=MARK)), \
         patch("miniapp.backend.routes.care.get_user_tz", AsyncMock(return_value=3)):
        yield
    with get_engine().begin() as c:
        c.execute(delete(care_periods).where(care_periods.c.user_id == MARK))


def put(body, headers=P):
    return TestClient(app).put("/api/care/period/auto", json=body, headers=headers)


def row():
    with get_engine().connect() as c:
        r = c.execute(text("SELECT starts_on, ends_on, source FROM care_periods WHERE user_id = :u"), {"u": MARK}).first()
    return None if r is None else (r.starts_on, r.ends_on, r.source)


def body(a, b):
    return {"starts_on": a.isoformat() if a else None, "ends_on": b.isoformat() if b else None}


# ── миграция ──────────────────────────────────────────────────────────────────

def _mig():
    path = next((REPO / "alembic" / "versions").glob("*_care_periods_source.py"))
    spec = importlib.util.spec_from_file_location("_mig_period_source", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_defaults_to_manual_checks_values_and_is_reversible_without_losing_periods():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _mig()
    col = {c["name"]: c for c in inspect(get_engine()).get_columns("care_periods")}["source"]
    assert col["nullable"] is False
    care.set_period(MARK, D(2026, 12, 1), D(2026, 12, 5))
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT source FROM care_periods WHERE user_id = :u"), {"u": MARK}).scalar() == "manual"
    with pytest.raises(Exception):                                              # CHECK: только manual | auto
        with get_engine().begin() as conn:
            conn.execute(text("UPDATE care_periods SET source = 'bogus' WHERE user_id = :u"), {"u": MARK})
    with get_engine().begin() as conn:                                          # вниз и вверх в ОДНОЙ транзакции: итог без изменений
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "source" not in {c["name"] for c in inspect(conn).get_columns("care_periods")}
            assert conn.execute(text("SELECT count(*) FROM care_periods WHERE user_id = :u"), {"u": MARK}).scalar() == 1      # период жив
            mig.upgrade()
    assert "source" in {c["name"] for c in inspect(get_engine()).get_columns("care_periods")}
    assert care.get_period(MARK)["source"] == "manual"


# ── поведение: auto и manual ──────────────────────────────────────────────────

def test_auto_sets_a_period_when_there_is_none_and_it_is_idempotent():
    r = put(body(D(2026, 12, 1), D(2026, 12, 20)))
    assert r.status_code == 200 and r.json() == {"ok": True, "applied": True, "changed": True, "reason": None,
                                                 "period": {"starts_on": "2026-12-01", "ends_on": "2026-12-20", "source": "auto"}}
    assert row() == (D(2026, 12, 1), D(2026, 12, 20), "auto")
    again = put(body(D(2026, 12, 1), D(2026, 12, 20))).json()
    assert again["applied"] is True and again["changed"] is False
    moved = put(body(D(2026, 12, 3), D(2026, 12, 25))).json()
    assert moved["changed"] is True and row() == (D(2026, 12, 3), D(2026, 12, 25), "auto")


def test_auto_never_overwrites_or_clears_a_manual_period():
    care.set_period(MARK, D(2026, 11, 1), D(2026, 11, 30))
    r = put(body(D(2027, 1, 1), D(2027, 1, 10))).json()
    assert r["applied"] is False and r["reason"] == "manual_override" and r["changed"] is False
    assert r["period"] == {"starts_on": "2026-11-01", "ends_on": "2026-11-30", "source": "manual"}
    c = put(body(None, None)).json()                                              # «периода нет» тоже не снимает ручной
    assert c["applied"] is False and c["reason"] == "manual_override"
    assert row() == (D(2026, 11, 1), D(2026, 11, 30), "manual")


def test_null_dates_clear_an_auto_period_and_are_a_noop_when_there_is_none():
    assert put(body(None, None)).json() == {"ok": True, "applied": True, "changed": False, "reason": None, "period": None}
    put(body(D(2026, 12, 1), D(2026, 12, 5)))
    r = put(body(None, None)).json()
    assert r["applied"] is True and r["changed"] is True and r["period"] is None and row() is None


def test_manual_set_takes_over_an_auto_period_and_manual_off_lets_auto_come_back():
    put(body(D(2026, 12, 1), D(2026, 12, 5)))
    care.set_period(MARK, D(2026, 12, 10), D(2026, 12, 12))                       # /care_period …
    assert row() == (D(2026, 12, 10), D(2026, 12, 12), "manual")
    assert put(body(D(2026, 12, 1), D(2026, 12, 5))).json()["applied"] is False
    assert care.clear_period(MARK) is True                                          # /care_period off
    assert put(body(D(2026, 12, 1), D(2026, 12, 5))).json()["applied"] is True and row()[2] == "auto"


def test_get_period_reports_the_source_and_the_schedule_follows_either_kind():
    today = datetime.now(timezone(timedelta(hours=3))).date()
    put(body(today - timedelta(days=1), today + timedelta(days=30)))
    assert care.get_period(MARK)["source"] == "auto" and care.is_active(MARK, 3) is True
    care.set_period(MARK, D(2020, 1, 1), D(2020, 1, 2))
    assert care.get_period(MARK)["source"] == "manual" and care.is_active(MARK, 3) is False


@pytest.mark.parametrize("a,b", [(D(2026, 12, 5), D(2026, 12, 1)), (D(2026, 1, 1), D(2027, 1, 20))])
def test_bad_dates_are_422_and_write_nothing(a, b):
    assert put(body(a, b)).status_code == 422 and row() is None


@pytest.mark.parametrize("payload", [{"starts_on": "2026-12-01"}, {"ends_on": "2026-12-01"}, {"starts_on": "2026-12-01", "ends_on": None},
                                     {"starts_on": "x", "ends_on": "y"}, {"starts_on": 5, "ends_on": 6}])
def test_one_date_without_the_other_or_garbage_is_422(payload):
    assert put(payload).status_code == 422 and row() is None


def test_period_length_boundary():
    assert put(body(D(2026, 12, 1), D(2026, 12, 1) + timedelta(days=370))).status_code == 200
    assert put(body(D(2026, 12, 1), D(2026, 12, 1) + timedelta(days=371))).status_code == 422


def test_unknown_owner_profile_is_404():
    with patch("miniapp.backend.routes.care_period_auto.get_user_id", AsyncMock(return_value=None)):
        assert put(body(D(2026, 12, 1), D(2026, 12, 5))).status_code == 404


def test_race_between_auto_and_manual_always_ends_manual():
    for i in range(15):
        with get_engine().begin() as c:
            c.execute(delete(care_periods).where(care_periods.c.user_id == MARK))
        gate = threading.Barrier(2)
        out = {}

        def auto():
            gate.wait(); out["a"] = care.apply_auto_period(MARK, D(2026, 12, 1), D(2026, 12, 5))

        def manual():
            gate.wait(); care.set_period(MARK, D(2026, 12, 10), D(2026, 12, 12))

        ts = [threading.Thread(target=auto), threading.Thread(target=manual)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert row() == (D(2026, 12, 10), D(2026, 12, 12), "manual"), i


# ── владелец видит источник ───────────────────────────────────────────────────

def test_owner_endpoints_show_source_and_manual_put_is_manual():
    app.dependency_overrides[current_user_id] = lambda: OWNER
    try:
        c = TestClient(app)
        put(body(D(2026, 12, 1), D(2026, 12, 5)))
        assert c.get("/api/care/period").json()["period"]["source"] == "auto"
        assert c.put("/api/care/period", json={"starts_on": "2026-12-10", "ends_on": "2026-12-12"}).json()["period"]["source"] == "manual"
        assert c.get("/api/care/period").json()["period"] == {"starts_on": "2026-12-10", "ends_on": "2026-12-12", "source": "manual"}
        assert c.delete("/api/care/period").status_code == 200 and row() is None
    finally:
        app.dependency_overrides.pop(current_user_id, None)


# ── токены: каждый открывает ровно свои маршруты ─────────────────────────────

def _opened_by(hdr):
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
            for m in (pgate, wgate, rgate, cats_gate):                     # перебор шлёт много неверных токенов: окно попыток не должно мешать
                m._limiter = m.FailureLimiter()
            with_token = client.request(method, path, headers=hdr, **kw).status_code
            without = client.request(method, path, **kw).status_code
            if with_token not in (401, 403) and without in (401, 403):
                opened.add((method, r.path))
    return opened


def test_every_token_opens_exactly_its_own_routes_and_the_sets_are_disjoint():
    period_set, write_set, read_set, cats_set = _opened_by(P), _opened_by(W), _opened_by(R), _opened_by(C)
    assert period_set == {("PUT", "/api/care/period/auto")}
    assert write_set == {("POST", "/api/care/tasks/{task_id}/done"), ("POST", "/api/care/tasks/{task_id}/postpone")}
    assert read_set == {("GET", "/api/sitter/lists"), ("GET", "/api/care/schedule"), ("POST", "/api/sitter/ask")}
    assert all(p.startswith("/api/cats") for _, p in cats_set)                       # токен котов: только карточки котов (его маршруты зависят от адреса клиента)
    sets = [period_set, write_set, read_set, cats_set]
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            assert not (a & b)


def test_no_other_token_opens_the_period_route_and_the_period_token_opens_nothing_else():
    for hdr in (W, R, C):
        assert put(body(D(2026, 12, 1), D(2026, 12, 5)), headers=hdr).status_code == 401
    assert row() is None
    c = TestClient(app)
    assert c.post("/api/care/tasks/1/done", json={}, headers=P).status_code == 401
    assert c.get("/api/care/schedule", headers=P).status_code == 401
    assert c.post("/api/sitter/ask", json={"q": "x"}, headers=P).status_code == 401
    assert c.get("/api/sitter/lists", headers=P).status_code == 401
    assert c.put("/api/care/period", json=body(D(2026, 12, 1), D(2026, 12, 5)), headers=P).status_code in (401, 403)      # владельческая ручка: только initData


@pytest.mark.parametrize("hdr", [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + PERIOD},
                                 {"Authorization": PERIOD}, {"Authorization": "Bearer " + PERIOD + "x"}, {"Authorization": "bearer"}])
def test_missing_or_malformed_credentials_are_401_without_falling_through(hdr):
    assert put(body(D(2026, 12, 1), D(2026, 12, 5)), headers=hdr).status_code == 401 and row() is None


@pytest.mark.parametrize("tok,owner,allowed", [("", OWNER, [OWNER]), ("short", OWNER, [OWNER]), (PERIOD, 0, [OWNER]), (PERIOD, OWNER, [700000001])])
def test_feature_off_when_misconfigured(monkeypatch, tok, owner, allowed):
    monkeypatch.setattr(config, "care_period_token", tok)
    monkeypatch.setattr(config, "care_period_owner_tg_id", owner)
    monkeypatch.setattr(config, "allowed_ids", allowed)
    assert put(body(D(2026, 12, 1), D(2026, 12, 5)), headers={"Authorization": f"Bearer {tok or 'x'}"}).status_code == 401 and row() is None


@pytest.mark.parametrize("other", ["cats_read_token", "sitter_lists_token", "care_write_token"])
def test_a_period_token_equal_to_any_other_token_disables_both_sides(monkeypatch, other):
    shared = PERIOD
    monkeypatch.setattr(config, other, shared)
    assert put(body(D(2026, 12, 1), D(2026, 12, 5))).status_code == 401 and row() is None                      # период выключен
    client = TestClient(app)
    if other == "sitter_lists_token":
        assert client.get("/api/care/schedule", headers=P).status_code == 401                                   # и читающий гейт тоже
    if other == "care_write_token":
        assert client.post("/api/care/tasks/1/done", json={}, headers=P).status_code == 401                     # и write-гейт тоже


def test_failed_attempts_are_limited_per_gate_and_do_not_lock_the_others():
    c = TestClient(app)
    for _ in range(10):
        assert c.put("/api/care/period/auto", json={}, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert put(body(D(2026, 12, 1), D(2026, 12, 5))).status_code == 429
    assert c.get("/api/care/schedule", headers=R).status_code == 200                                              # своё окно у каждого гейта
    assert c.post("/api/care/tasks/1/done", json={}, headers=W).status_code != 429


def test_response_is_not_cached_and_the_token_never_reaches_the_logs(caplog):
    caplog.set_level("DEBUG")
    r = put(body(D(2026, 12, 1), D(2026, 12, 5)))
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    put(body(D(2026, 12, 1), D(2026, 12, 5)), headers={"Authorization": f"Bearer {PERIOD}x"})
    assert PERIOD not in caplog.text and "2026-12" not in caplog.text                                              # ни токен, ни даты в логах


def test_get_is_not_allowed_on_the_auto_route():
    assert TestClient(app).get("/api/care/period/auto", headers=P).status_code in (401, 405)
