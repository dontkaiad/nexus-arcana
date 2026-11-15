"""Алерты ops-группы Nexus / Arcana / Zarya: общий шаблон, без пользовательских данных, транзиентные
только считаются, billing/auth ещё и в топик, тихий старт, workflow через ops-notify.

Сеть подменена. Конфиг лог-группы задаётся патчем config.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from core import ops_alert, ops_transient
from core.config import config

REPO = Path(__file__).resolve().parent.parent
SECRET = "ЛИЧНОЕ: клиент Анна, расклад на любовь, sk-ant-SECRETKEY"


@pytest.fixture(autouse=True)
def _topics(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "log_bot_token", "123:TESTTOKEN")
    monkeypatch.setattr(config, "log_chat_id", "-1001")
    monkeypatch.setattr(config, "log_thread_nexus", "9")
    monkeypatch.setattr(config, "log_thread_arcana", "10")
    monkeypatch.setattr(config, "log_thread_booking", "1182")
    monkeypatch.setattr(config, "log_thread_kaimcp", "2219")
    ops_alert._last_sent.clear()
    monkeypatch.setattr(ops_transient, "_tracker", ops_transient.TransientTracker(path=str(tmp_path / "t.json")))


@pytest.fixture()
def sent(monkeypatch):
    """Каждое сообщение, ушедшее в лог-группу: {text, thread, silent}."""
    out = []

    async def fake_send_log(text, thread_id="", *, silent=False):
        out.append({"text": text, "thread": thread_id, "silent": silent})
        return 4242

    monkeypatch.setattr("core.bot_notify._send_log", fake_send_log)
    return out


# --- шаблон -------------------------------------------------------------------

def test_template_shape_matches_ops_notify():
    assert ops_alert.format_alert("nexus", "🔴", "error", "title", "body") == \
        "🔴 <b>☀️ nexus</b> · error\ntitle\n<i>body</i>"
    assert ops_alert.format_alert("arcana", "🟢", "recovered") == "🟢 <b>🌒 arcana</b> · recovered"
    assert ops_alert.format_alert("zarya", "🟠", "warning", "a <b> & c").endswith("a &lt;b&gt; &amp; c")
    assert ops_alert.format_event("kai-mcp", "🤖", "claude", "текст <b>как есть</b>") == \
        "🤖 <b>🔌 kai-mcp</b> · claude\nтекст <b>как есть</b>"


def test_bot_key_and_topics():
    assert [ops_alert.bot_key(x) for x in ("☀️ Nexus", "🌒 Arcana", "⭐ Zarya", "", "booking")] == \
        ["nexus", "arcana", "zarya", "nexus", "zarya"]
    assert (ops_alert.thread_for("nexus"), ops_alert.thread_for("arcana"),
            ops_alert.thread_for("zarya"), ops_alert.thread_for("kai-mcp")) == ("9", "10", "1182", "2219")


@pytest.mark.asyncio
async def test_send_alert_goes_to_the_topic_of_the_bot_and_dedupes(sent):
    assert await ops_alert.send_alert("zarya", "🔴", "error", "t", "b") == 4242
    assert await ops_alert.send_alert("zarya", "🔴", "error", "t", "b") is None       # тот же текст: молчим
    await ops_alert.send_alert("arcana", "🔴", "error", "t", "b")
    assert [m["thread"] for m in sent] == ["1182", "10"]


# --- без пользовательских данных -----------------------------------------------

def test_format_for_group_carries_no_user_text_no_claude_text_no_exception_text():
    from core.error_log import _format_for_group

    tb = ('Traceback (most recent call last):\n  File "/app/arcana/handlers/sessions.py", line 1110, in handle\n'
          f'    raise ValueError(text)\nValueError: {SECRET}\n')
    text = _format_for_group(SECRET, "parse_error", SECRET, tb, "🌒 Arcana", "E1", "handle_session")
    assert "ЛИЧНОЕ" not in text and "SECRETKEY" not in text and "Анна" not in text
    assert "msg:" not in text and "claude:" not in text and "<pre>" not in text
    assert text.startswith("🔴 <b>🌒 arcana</b> · error\nparse_error · E1\n<i>ValueError · sessions.py:1110 in handle · handle_session</i>")


@pytest.mark.parametrize("fake_tb", [
    "Expecting value: line 1 column 1 (char 0)",          # str(e), как его передаёт classifier
    "Привет: мир",
    "Test: расклад Анны",                                 # не заканчивается на Error/Exception: не класс
    "",
])
def test_tb_summary_does_not_take_free_text_for_a_class(fake_tb):
    cls, where = ops_alert.tb_summary(fake_tb)
    assert cls == "" and where == ""


def test_tb_summary_takes_the_last_frame_and_a_strict_class():
    tb = ('  File "/app/core/a.py", line 3, in outer\n  File "/app/core/b.py", line 77, in inner\n'
          "anthropic.APIConnectionError: Connection error with a user text\n")
    assert ops_alert.tb_summary(tb) == ("APIConnectionError", "b.py:77 in inner")


@pytest.mark.asyncio
async def test_log_error_alert_has_no_user_text_but_the_container_log_keeps_it(sent, caplog):
    from core import error_log

    with caplog.at_level(logging.ERROR, logger="bot.errors"):
        await error_log.log_error(SECRET, "processing_error", SECRET, "", bot_label="☀️ Nexus", error_code="–")
    assert len(sent) == 1 and "ЛИЧНОЕ" not in sent[0]["text"] and sent[0]["thread"] == "9"
    assert any("ЛИЧНОЕ" in r.getMessage() for r in caplog.records)      # полный текст остался в docker logs


def test_exc_label_and_where_hold_class_status_and_place_only():
    class Boom(Exception):
        status_code = 529

    try:
        raise Boom(SECRET)
    except Boom as exc:
        assert ops_alert.exc_label(exc) == "Boom (HTTP 529)"
        assert re.fullmatch(r"test_ops_alerts\.py:\d+ in test_exc_label_and_where_hold_class_status_and_place_only",
                            ops_alert.exc_where(exc))


# --- события «как есть», только в шаблоне ---------------------------------------

@pytest.mark.asyncio
async def test_booking_audit_line_keeps_its_text_under_the_template_header():
    from core import bot_notify

    got = []

    async def fake(text, thread_id="", *, silent=False):
        got.append((text, thread_id))
        return True

    with patch.object(bot_notify, "notify_log_group", fake):
        await bot_notify.notify_booking_log("🃏 Заявка #5 (Маша) — подтверждена")
    assert got == [("📅 <b>⭐ zarya</b> · booking\n🃏 Заявка #5 (Маша) — подтверждена", "1182")]


def test_handle_voice_sends_the_transcript_through_the_template_header_in_full():
    src = (REPO / "arcana" / "bot.py").read_text(encoding="utf-8")
    body = src[src.index("async def handle_voice"):src.index("async def handle_photo")]
    assert 'format_event("arcana", "🎤", "voice"' in body and "[:1500]" not in body and "split_text(" in body


def test_voice_transcript_goes_in_full_under_the_template_header():
    long = "слово " * 500
    text = ops_alert.format_event("arcana", "🎤", "voice", "транскрипт: " + long)
    assert text.startswith("🎤 <b>🌒 arcana</b> · voice\nтранскрипт: слово слово")
    assert text.endswith(long) and len(text) < 4096


# --- транзиентные сбои ---------------------------------------------------------

def _aiogram_errors():
    from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
    from aiogram.methods import GetMe

    m = GetMe()
    return (TelegramNetworkError(method=m, message="Request timeout error"),
            TelegramRetryAfter(method=m, message="flood", retry_after=3),
            TelegramBadRequest(method=m, message="message is too long"))


def test_real_aiogram_classes_are_classified():
    net, retry, bad = _aiogram_errors()
    assert ops_transient.is_transient(net) and ops_transient.is_transient(retry)
    assert not ops_transient.is_transient(bad) and not ops_transient.is_transient(ValueError("x"))


def test_a_bad_request_is_never_transient_even_under_a_network_class():
    class TelegramNetworkError(Exception):      # форма PTB: «плохой запрос» наследует сетевой класс
        pass

    class TelegramBadRequest(TelegramNetworkError):
        pass

    assert ops_transient.is_transient(TelegramNetworkError("x")) is True
    assert ops_transient.is_transient(TelegramBadRequest("x")) is False


def _record(name="aiogram.dispatcher", msg="boom", exc=None):
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    return logging.LogRecord(name, logging.ERROR, __file__, 1, msg, (), exc_info)


def test_classify_record_for_the_aiogram_polling_message_and_exceptions():
    poll = _record(msg="Failed to fetch updates - TelegramNetworkError: HTTP Client says - Request timeout error")
    assert ops_transient.classify_record(poll) == "TelegramNetworkError"
    assert ops_transient.classify_record(_record(msg="Failed to fetch updates - TelegramBadRequest: x")) is None
    assert ops_transient.classify_record(_record(name="nexus.handlers", msg="TelegramNetworkError in our code")) is None
    net, _, bad = _aiogram_errors()
    assert ops_transient.classify_record(_record(name="x", exc=net)) == "TelegramNetworkError"
    assert ops_transient.classify_record(_record(name="x", exc=bad)) is None
    assert ops_transient.classify_record(_record(name="x", msg="plain error")) is None


def _tracker(tmp_path, clock):
    return ops_transient.TransientTracker(path=str(tmp_path / "t.json"), clock=lambda: clock["t"])


def test_burst_alerts_once_then_recovers_after_quiet(tmp_path):
    clock = {"t": 1_000_000.0}
    tr = _tracker(tmp_path, clock)
    results = []
    for _ in range(7):
        results.append(tr.record("TelegramNetworkError"))
        clock["t"] += 10
    alerts = [r for r in results if r]
    assert len(alerts) == 1 and results[4] == alerts[0]
    assert alerts[0][1] == "warning" and "5" in alerts[0][2] and "TelegramNetworkError" in alerts[0][3]
    assert tr.tick() is None
    clock["t"] += ops_transient.QUIET_SECONDS
    rec = tr.tick()
    assert rec and rec[1] == "recovered" and "7" in rec[3]
    assert tr.tick() is None


def test_counters_file_is_the_format_the_pulse_reads(tmp_path):
    clock = {"t": 1_000_000.0}
    tr = _tracker(tmp_path, clock)
    tr.record("TelegramNetworkError")
    tr.record("TelegramNetworkError")
    data = json.loads((tmp_path / "t.json").read_text())
    assert data["counts"] == {"TelegramNetworkError": 2} and data["day"] and "prev_counts" in data
    clock["t"] += 86400
    tr.record("TelegramNetworkError")
    data = json.loads((tmp_path / "t.json").read_text())
    assert data["prev_counts"] == {"TelegramNetworkError": 2} and data["counts"] == {"TelegramNetworkError": 1}


@pytest.mark.asyncio
async def test_handler_counts_transient_records_alerts_once_per_burst_and_still_mirrors_real_errors(sent, tmp_path):
    from core import logging_notion

    logging_notion._recent.clear()
    clock = {"t": 2_000_000.0}
    ops_transient._tracker = _tracker(tmp_path, clock)
    h = logging_notion.StructuredErrorHandler(bot_label="🌒 Arcana")
    for i in range(5):
        h.emit(_record(msg=f"Failed to fetch updates - TelegramNetworkError: HTTP Client says - Request timeout {i}"))
        clock["t"] += 5
    h.emit(_record(name="arcana.handlers", msg="real bug", exc=ValueError(SECRET)))
    await asyncio.sleep(0.05)
    words = [m["text"].split("\n")[0] for m in sent]
    assert words == ["🟠 <b>🌒 arcana</b> · warning", "🔴 <b>🌒 arcana</b> · error"]      # один всплеск и одна настоящая ошибка
    assert all(m["thread"] == "10" for m in sent)
    assert "ЛИЧНОЕ" not in sent[1]["text"] and "ValueError" in sent[1]["text"]
    assert ops_transient.tracker().counts["TelegramNetworkError"] == 5


@pytest.mark.asyncio
async def test_heartbeat_tick_sends_the_recovered_message(sent, tmp_path):
    from core import logging_notion

    clock = {"t": 3_000_000.0}
    tr = _tracker(tmp_path, clock)
    ops_transient._tracker = tr
    logging_notion._remember("⭐ Zarya")
    for _ in range(5):
        tr.record("TelegramNetworkError")
    clock["t"] += ops_transient.QUIET_SECONDS
    await logging_notion.tick()
    assert len(sent) == 1 and sent[0]["thread"] == "1182"
    assert sent[0]["text"].startswith("🟢 <b>⭐ zarya</b> · recovered")


@pytest.mark.asyncio
async def test_the_heartbeat_loop_calls_the_recovery_tick(monkeypatch, tmp_path):
    from core import heartbeat, logging_notion

    ticks = []

    async def fake_tick():
        ticks.append(1)

    monkeypatch.setattr(logging_notion, "tick", fake_tick)
    task = asyncio.ensure_future(heartbeat._heartbeat_loop(str(tmp_path / "hb"), 0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(ticks) >= 2 and (tmp_path / "hb").exists()


# --- billing / auth: ещё и в топик Nexus -----------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("kind,needle", [("billing", "кончились кредиты"), ("auth", "ключ отклонён")])
async def test_billing_and_auth_go_to_the_dm_and_to_the_nexus_topic(sent, monkeypatch, kind, needle):
    from core import llm_health

    dm = AsyncMock(return_value=True)
    monkeypatch.setattr("core.bot_notify.notify_user", dm)
    monkeypatch.setattr(config, "allowed_ids", [11, 22])
    await llm_health._send_alert(kind)
    assert [c.args[0] for c in dm.await_args_list] == [11, 22]            # личка прежняя, на оба аккаунта
    assert len(sent) == 1 and sent[0]["thread"] == "9"                    # и топик Nexus
    assert sent[0]["text"].startswith("🔴 <b>☀️ nexus</b> · error") and needle in sent[0]["text"]


@pytest.mark.asyncio
async def test_a_dead_ops_chat_does_not_break_the_dm_alert(monkeypatch):
    from core import llm_health

    dm = AsyncMock(return_value=True)
    monkeypatch.setattr("core.bot_notify.notify_user", dm)
    monkeypatch.setattr(config, "allowed_ids", [11])

    async def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(ops_alert, "send_alert", boom)
    await llm_health._send_alert("billing")
    assert dm.await_count == 1


# --- тихий старт -------------------------------------------------------------------

@pytest.mark.parametrize("path", ["nexus/nexus_bot.py", "arcana/bot.py", "zarya/bot.py", "core/bot_notify.py"])
def test_no_startup_ping_in_any_bot(path):
    src = (REPO / path).read_text(encoding="utf-8")
    assert "notify_startup" not in src and "поднялся\\n" not in src and 'f"{label} поднялся' not in src


def test_every_bot_still_logs_its_version_and_zarya_is_on_the_common_handler():
    assert 'log_version("nexus")' in (REPO / "nexus/nexus_bot.py").read_text(encoding="utf-8")
    assert 'log_version("arcana")' in (REPO / "arcana/bot.py").read_text(encoding="utf-8")
    z = (REPO / "zarya/bot.py").read_text(encoding="utf-8")
    assert 'log_version("zarya")' in z and '_install_notion_logging(bot_label="⭐ Zarya")' in z


def test_zarya_image_copies_all_of_core_so_the_new_modules_reach_it():
    assert "COPY core/ ./core/" in (REPO / "zarya/Dockerfile").read_text(encoding="utf-8")


# --- живой тест -----------------------------------------------------------------

def test_ops_test_cases_cover_every_template_and_use_the_real_builders():
    sys.path.insert(0, str(REPO))
    from scripts import ops_test

    cases = ops_test._cases()
    assert len(cases) == 9 and {b for b, _ in cases} == {"nexus", "arcana", "zarya", "kai-mcp"}
    heads = [t.split("\n")[0] for _, t in cases]
    assert heads[0].startswith("🔴 <b>☀️ nexus</b>") and heads[2].startswith("🔴 <b>⭐ zarya</b>")
    assert all("<b>" in h for h in heads)
