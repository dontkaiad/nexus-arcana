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


# ── сбор срабатывает ДО вызова LLM: при упавшем API (billing) строка всё равно пишется ──────────────────────────

BILLING_MSG = "Your credit balance is too low to access the Anthropic API."


def _billing_error():
    import anthropic
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req, json={"type": "error", "error": {"message": BILLING_MSG}})
    return anthropic.BadRequestError(BILLING_MSG, response=resp, body={"error": {"message": BILLING_MSG}})


def test_capture_happens_before_llm_even_when_api_is_down(monkeypatch):
    """classify() первой строкой зовёт сбор, а уже потом ходит в LLM: при нулевом балансе строка в файле есть."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from core import claude_client as cc, classifier, llm_health

    capture.enable(limit=10, ttl_days=1)
    with patch.object(cc, "_create_message", AsyncMock(side_effect=_billing_error())), \
            patch.object(llm_health, "_send_alert", AsyncMock()):
        asyncio.run(classifier.classify("купить молоко завтра", tz_offset=3, user_id="u-test"))
    asyncio.set_event_loop(asyncio.new_event_loop())               # asyncio.run сбрасывает loop для соседних тестов
    got = lines()
    assert [g["text"] for g in got] == ["купить молоко завтра"] and "user_id" not in got[0]
    assert capture.status()["count"] == 1


def test_capture_on_the_bot_text_path_with_billing_error(monkeypatch):
    """Путь бота: handle_text → нормализация (тоже LLM) → classify. API лежит — сообщение всё равно собрано."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from core import claude_client as cc, llm_health
    import nexus.nexus_bot as nb

    capture.enable(limit=10, ttl_days=1)
    msg = SimpleNamespace(
        text="напомни позвонить маме", from_user=SimpleNamespace(id=700000001, is_bot=False),
        chat=SimpleNamespace(id=700000001), reply_to_message=None, voice=None, photo=None,
        bot=SimpleNamespace(send_chat_action=AsyncMock()), answer=AsyncMock(), reply=AsyncMock())
    no = AsyncMock(return_value=False)
    with patch.object(cc, "_create_message", AsyncMock(side_effect=_billing_error())), \
            patch.object(llm_health, "_send_alert", AsyncMock()), \
            patch("nexus.handlers.finance.maybe_payday_reminder", AsyncMock()), \
            patch("nexus.handlers.finance.handle_budget_setup_text", no), \
            patch("nexus.handlers.finance.handle_finance_pending", no), \
            patch("nexus.handlers.lists.handle_list_pending", no), \
            patch.object(nb, "_handle_receipt_clarify", no), \
            patch("nexus.handlers.tasks.maybe_handle_reschedule_pending", no), \
            patch("nexus.handlers.tasks._pending_has", lambda *_: False), \
            patch("nexus.handlers.tasks._get_effective_tz", AsyncMock(return_value=(3, "MSK"))), \
            patch("nexus.handlers.utils.react", AsyncMock()), \
            patch.object(nb, "process_item", AsyncMock(return_value="")), \
            patch.object(nb, "handle_last_task_clarify", no, create=True):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(nb.handle_text(msg, user_id="u-test"))
        except Exception:
            pass                                                   # что бот ответит дальше — не предмет этого теста
        finally:
            loop.close()
            asyncio.set_event_loop(asyncio.new_event_loop())
    assert [g["text"] for g in lines()] == ["напомни позвонить маме"]
