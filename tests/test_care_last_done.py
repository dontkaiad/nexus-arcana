"""tests/test_care_last_done.py — блок «последний раз»: tasks.care_label + ключ `care` в GET /api/cats.

Матрица (нет выполнений, одно, несколько, чужой владелец, не for_sitter, пустая метка, архив), утечка полей, метка (set_care_label),
миграция, изоляция токенов. Реальная БД, вымышленные данные, токены вымышленные."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from core import care
from core.config import config
from core.db import get_engine
from miniapp.backend import care_period_auth as pgate
from miniapp.backend import care_write_auth as wgate
from miniapp.backend import cats_read_auth as cgate
from miniapp.backend import sitter_lists_auth as rgate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

REPO = Path(__file__).resolve().parent.parent
MARK, OTHER = "test-care-last-done", "test-care-last-done-other"
OWNER = 700000125
CATS, READ, WRITE, PERIOD = ("test-only-%s-" % n + k * 12 for n, k in (("cats", "a1"), ("sitter", "b2"), ("write", "c3"), ("period", "d4")))
UTC = timezone.utc


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "cats_read_token", CATS)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "sitter_lists_token", READ)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "care_write_token", WRITE)
    monkeypatch.setattr(config, "care_write_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "care_period_token", PERIOD)
    monkeypatch.setattr(config, "care_period_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    for m in (cgate, rgate, wgate, pgate):
        monkeypatch.setattr(m, "_limiter", m.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks WHERE user_id IN (:a, :b, '') AND care_label IS NOT NULL"), {"a": MARK, "b": OTHER})


def mk(label="лотки", user=MARK, for_sitter=True, status="Not started", title="Секретное название задачи", note="секретная заметка"):
    with get_engine().begin() as c:
        sid = c.execute(text("SELECT id FROM task_status WHERE code = :c"), {"c": status}).scalar()
        return c.execute(text("INSERT INTO tasks (title, status_id, user_id, for_sitter, care_label, note) VALUES (:t, :s, :u, :f, :l, :n) RETURNING id"),
                         {"t": title, "s": sid, "u": user, "f": for_sitter, "l": label, "n": note}).scalar()


def done(tid, at, source="bot", by="имя-исполнителя"):
    with get_engine().begin() as c:
        c.execute(text("INSERT INTO task_completions (task_id, done_at, done_by, source) VALUES (:i, :a, :b, :s)"), {"i": tid, "a": at, "b": by, "s": source})


def api(path="/api/cats", params=None, hdr=None):
    return TestClient(app).get(path, params=params, headers=hdr if hdr is not None else {"Authorization": f"Bearer {CATS}"})


T1 = datetime(2026, 11, 7, 13, 13, 23, tzinfo=UTC)
T2 = datetime(2026, 11, 8, 15, 50, 46, tzinfo=UTC)


# ── матрица ───────────────────────────────────────────────────────────────────

def test_no_tasks_means_an_empty_list():
    assert care.care_summary(MARK) == [] and api().json()["care"] == []


def test_task_without_completions_has_nulls():
    mk("вода")
    assert api().json()["care"] == [{"label": "вода", "last_done_at": None, "last_done_by": None}]


def test_one_completion():
    tid = mk()
    done(tid, T1, "bot")
    assert api().json()["care"] == [{"label": "лотки", "last_done_at": "2026-11-07T13:13:23Z", "last_done_by": "owner"}]


def test_several_completions_take_the_latest_and_its_source_decides_who():
    tid = mk()
    done(tid, T2, "sitter"); done(tid, T1, "bot"); done(tid, T1 - timedelta(days=3), "miniapp")
    assert api().json()["care"] == [{"label": "лотки", "last_done_at": "2026-11-08T15:50:46Z", "last_done_by": "sitter"}]
    done(tid, T2 + timedelta(hours=1), "miniapp")                                # позже отметила владелец
    c = api().json()["care"][0]
    assert c["last_done_at"] == "2026-11-08T16:50:46Z" and c["last_done_by"] == "owner"


@pytest.mark.parametrize("source,who", [("sitter", "sitter"), ("bot", "owner"), ("miniapp", "owner"), ("status", "owner"), ("app", "owner"), ("", "owner"), ("weird", "owner")])
def test_only_the_sitter_source_counts_as_the_sitter(source, who):
    done(mk(), T1, source)
    assert care.care_summary(MARK)[0]["last_done_by"] == who


def test_ties_on_time_are_resolved_by_the_later_row():
    tid = mk()
    done(tid, T1, "bot"); done(tid, T1, "sitter")
    assert care.care_summary(MARK)[0]["last_done_by"] == "sitter"


def test_completions_of_other_tasks_do_not_mix():
    a, b = mk("лотки"), mk("вода")
    done(a, T1, "bot"); done(b, T2, "sitter")
    assert [(c["label"], c["last_done_by"]) for c in care.care_summary(MARK)] == [("лотки", "owner"), ("вода", "sitter")]    # порядок создания


@pytest.mark.parametrize("kw", [{"user": OTHER}, {"for_sitter": False}, {"label": None}, {"label": ""}, {"label": "   "}, {"status": "Archived"}])
def test_excluded_rows(kw):
    tid = mk(**kw)
    done(tid, T1, "sitter")
    assert care.care_summary(MARK) == [] and api().json()["care"] == []


def test_done_and_in_progress_tasks_are_included_archived_is_not():
    for st in ("Done", "In progress", "Not started"):
        mk("лотки-" + st[:2], status=st)
    assert len(care.care_summary(MARK)) == 3


def test_other_owner_is_isolated_both_ways():
    mk("мои"); mk("чужие", user=OTHER)
    assert [c["label"] for c in care.care_summary(MARK)] == ["мои"] and [c["label"] for c in care.care_summary(OTHER)] == ["чужие"]
    legacy = mk("без-владельца", user="")                                              # старые строки без владельца никому не показываются
    assert care.care_summary("") == [] and legacy


def test_label_is_clipped_and_cleaned_on_output():
    with get_engine().begin() as c:
        c.execute(text("ALTER TABLE tasks DROP CONSTRAINT tasks_care_label_len"))
    try:
        tid = mk("\x07лотки\n" + "я" * 60)
        done(tid, T1)
        lab = care.care_summary(MARK)[0]["label"]
        assert len(lab) <= 30 and "\x07" not in lab and "\n" not in lab
    finally:
        with get_engine().begin() as c:
            c.execute(text("DELETE FROM tasks WHERE user_id = :u"), {"u": MARK})
            c.execute(text("ALTER TABLE tasks ADD CONSTRAINT tasks_care_label_len CHECK (care_label IS NULL OR char_length(care_label) <= 30)"))


# ── утечка полей ──────────────────────────────────────────────────────────────

def test_only_three_fields_and_nothing_else_leaks():
    tid = mk(title="Секретное название задачи", note="секретная заметка")
    done(tid, T1, "sitter", by="имя-исполнителя")
    body = api().json()
    (entry,) = body["care"]
    assert set(entry) == {"label", "last_done_at", "last_done_by"}
    flat = json.dumps(body["care"], ensure_ascii=False)
    for secret in ("Секретное", "секретная", "имя-исполнителя", MARK, str(tid), "user_id", "\"source\""):
        assert secret not in flat


def test_with_facts_flag_does_not_change_the_care_block():
    done(mk(), T1, "bot")
    a = api(params={"with_facts": 1}).json()["care"]
    b = api().json()["care"]
    assert a == b and a[0]["last_done_by"] == "owner"


# ── доступ ────────────────────────────────────────────────────────────────────

def test_the_cats_token_and_the_owner_see_it_and_the_other_tokens_do_not():
    done(mk(), T1, "sitter")
    assert api().status_code == 200 and api().json()["care"][0]["label"] == "лотки"
    for tok in (READ, WRITE, PERIOD, "wrong"):
        assert api(hdr={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert api(hdr={}).status_code in (401, 403)
    app.dependency_overrides[current_user_id] = lambda: OWNER                       # владелец через Mini App
    try:
        r = TestClient(app).get("/api/cats")
        assert r.status_code == 200 and r.json()["care"][0]["last_done_by"] == "sitter"
    finally:
        app.dependency_overrides.pop(current_user_id, None)


def test_care_key_is_in_the_cats_list_only():
    done(mk(), T1)
    for path in ("/api/care/schedule", "/api/sitter/lists"):
        r = TestClient(app).get(path, headers={"Authorization": f"Bearer {READ}"})
        assert "care" not in (r.json() if r.status_code == 200 else {})


# ── метка: запись ─────────────────────────────────────────────────────────────

def test_set_care_label_only_for_own_sitter_tasks_and_validates():
    tid = mk(label=None)
    assert care.set_care_label(MARK, tid, "  лотки  ") is True and care.care_summary(MARK)[0]["label"] == "лотки"
    assert care.set_care_label(MARK, tid, "я" * 30) is True
    with pytest.raises(ValueError):
        care.set_care_label(MARK, tid, "я" * 31)
    assert care.set_care_label(OTHER, tid, "чужая") is False                         # чужая задача
    plain = mk(label=None, for_sitter=False)
    assert care.set_care_label(MARK, plain, "лотки") is False                        # не в расписании ситтера
    assert care.set_care_label(MARK, 987654321, "лотки") is False
    assert care.set_care_label(MARK, tid, None) is True and care.care_summary(MARK) == []
    assert care.set_care_label(MARK, tid, "\x07\n") is True and care.care_summary(MARK) == []      # только управляющие символы = пусто


def test_removing_the_sitter_flag_clears_the_label():
    tid = mk()
    assert care.set_task_flag(MARK, tid, False) is True
    with get_engine().connect() as c:
        assert c.execute(text("SELECT care_label FROM tasks WHERE id = :i"), {"i": tid}).scalar() is None
    care.set_task_flag(MARK, tid, True)
    assert care.care_summary(MARK) == []                                              # метка не вернулась сама


def test_database_refuses_a_31_char_label():
    with pytest.raises(Exception):
        mk("я" * 31)


# ── миграция ──────────────────────────────────────────────────────────────────

def test_migration_adds_a_nullable_column_and_is_reversible_without_losing_tasks():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = next((REPO / "alembic" / "versions").glob("*_tasks_care_label.py"))
    spec = importlib.util.spec_from_file_location("_mig_care_label", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    col = {c["name"]: c for c in inspect(get_engine()).get_columns("tasks")}["care_label"]
    assert col["nullable"] is True
    tid = mk(label=None)
    with get_engine().begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "care_label" not in {c["name"] for c in inspect(conn).get_columns("tasks")}
            assert conn.execute(text("SELECT count(*) FROM tasks WHERE id = :i"), {"i": tid}).scalar() == 1
            mig.upgrade()
    assert "care_label" in {c["name"] for c in inspect(get_engine()).get_columns("tasks")}
