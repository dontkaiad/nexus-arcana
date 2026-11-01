"""tests/test_eval_capture.py — сбор сообщений для эвала классификатора: выключен по умолчанию, лимит, само-выключение,
срок хранения файла, user_id не пишется, сбой не мешает.
"""
from __future__ import annotations

import json

import pytest

from evals import capture


@pytest.fixture(autouse=True)
def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "STATE", tmp_path / "capture.json")
    monkeypatch.setattr(capture, "FILE", tmp_path / "captured_messages.jsonl")
    monkeypatch.delenv("EVAL_CAPTURE", raising=False)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(capture, "_now", lambda: clock["t"])
    return clock


def lines():
    return [json.loads(l) for l in capture.FILE.read_text(encoding="utf-8").splitlines() if l.strip()] if capture.FILE.exists() else []


def test_off_by_default_writes_nothing():
    capture.maybe_capture("секретное сообщение")
    assert not capture.FILE.exists() and not capture.STATE.exists()
    assert capture.status()["state"] == "off"


def test_enable_collects_text_only_without_user_id():
    capture.enable(limit=5, ttl_days=14)
    capture.maybe_capture("первое сообщение")
    rows = lines()
    assert [r["text"] for r in rows] == ["первое сообщение"] and set(rows[0]) == {"ts", "text"}
    assert capture.status()["count"] == 1 and capture.status()["state"] == "collecting"


def test_long_text_is_truncated():
    capture.enable()
    capture.maybe_capture("я" * 5000)
    assert len(lines()[0]["text"]) == capture.MAX_CHARS


def test_switches_itself_off_after_the_limit_and_keeps_the_file():
    capture.enable(limit=3)
    for i in range(10):
        capture.maybe_capture(f"сообщение {i}")
    assert len(lines()) == 3                                  # ровно лимит, дальше не пишет
    st = capture.status()
    assert st["enabled"] is False and st["state"] == "done" and st["count"] == 3 and st["file_lines"] == 3


def test_file_and_state_expire_after_ttl(_paths):
    capture.enable(limit=100, ttl_days=14)
    capture.maybe_capture("до срока")
    _paths["t"] += 13 * 86400
    capture.maybe_capture("ещё в срок")
    assert len(lines()) == 2
    _paths["t"] += 2 * 86400                                   # прошло 15 дней > 14
    capture.maybe_capture("после срока")
    assert not capture.FILE.exists() and not capture.STATE.exists()


def test_finished_collection_is_still_purged_by_ttl(_paths):
    capture.enable(limit=1, ttl_days=14)
    capture.maybe_capture("единственное")
    assert capture.status()["state"] == "done" and capture.FILE.exists()
    _paths["t"] += 15 * 86400
    capture.maybe_capture("любое следующее сообщение запускает проверку срока")
    assert not capture.FILE.exists() and not capture.STATE.exists()


def test_env_flag_enables_with_the_same_limits(monkeypatch):
    monkeypatch.setenv("EVAL_CAPTURE", "1")
    capture.maybe_capture("через переменную")
    st = capture.status()
    assert st["state"] == "collecting" and st["limit"] == capture.DEFAULT_LIMIT      # «забытая» переменная тоже ограничена
    assert lines()[0]["text"] == "через переменную"


def test_disable_and_purge():
    capture.enable()
    capture.maybe_capture("x")
    assert capture.disable()["state"] == "done"
    capture.maybe_capture("после выключения")
    assert len(lines()) == 1
    assert capture.purge(force=True) is True and not capture.FILE.exists()


def test_failures_never_propagate(monkeypatch):
    capture.enable()
    monkeypatch.setattr(capture, "FILE", capture.FILE.parent / "no" / "such" / "dir" / "x.jsonl")
    monkeypatch.setattr(capture.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")), raising=False)
    capture.maybe_capture("не должно упасть")                   # без исключения


def test_classifier_hook_uses_the_capture_module(monkeypatch):
    from core import classifier
    capture.enable()
    classifier._capture_for_eval("сообщение из classify")
    assert [r["text"] for r in lines()] == ["сообщение из classify"]
