"""Тесты отправки ошибок Nexus/Arcana в общую TG-группу логов.

Развилка Б: шлём токеном ОБЩЕГО лог-бота (LOG_BOT_TOKEN) в группу
(LOG_CHAT_ID), Nexus и Arcana — в свои топики форума (per-bot).

Покрываем:
- notify_log_group постит в группу с message_thread_id и токеном лог-бота;
- пустой LOG_BOT_TOKEN → не шлёт, не падает (фича опциональна);
- сетевой сбой проглатывается → False;
- log_error роутит nexus→thread_nexus, arcana→thread_arcana.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class _Resp:
    status_code = 200
    text = "ok"


class _CaptureClient:
    """Мок httpx.AsyncClient, перехватывающий POST."""
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        _CaptureClient.captured = {"url": url, "json": json}
        return _Resp()


class _BoomClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        raise RuntimeError("network down")

    async def __aexit__(self, *a):
        return False


# ── notify_log_group (unit) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_log_group_posts_with_thread():
    from core import bot_notify
    _CaptureClient.captured = {}
    with patch.object(bot_notify.config, "log_bot_token", "999:LOGTOKEN"), \
         patch.object(bot_notify.config, "log_chat_id", "-1001234567890"), \
         patch.object(bot_notify.httpx, "AsyncClient", _CaptureClient):
        ok = await bot_notify.notify_log_group("<b>boom</b>", thread_id="55")
    assert ok is True
    cap = _CaptureClient.captured
    assert "/bot999:LOGTOKEN/sendMessage" in cap["url"]
    assert cap["json"]["chat_id"] == "-1001234567890"
    assert cap["json"]["message_thread_id"] == 55  # int, не строка
    assert cap["json"]["text"] == "<b>boom</b>"
    assert cap["json"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_notify_log_group_no_thread_omits_field():
    from core import bot_notify
    _CaptureClient.captured = {}
    with patch.object(bot_notify.config, "log_bot_token", "t"), \
         patch.object(bot_notify.config, "log_chat_id", "-100"), \
         patch.object(bot_notify.httpx, "AsyncClient", _CaptureClient):
        ok = await bot_notify.notify_log_group("x", thread_id="")
    assert ok is True
    assert "message_thread_id" not in _CaptureClient.captured["json"]


@pytest.mark.asyncio
async def test_notify_log_group_disabled_when_no_token():
    """Пустой LOG_BOT_TOKEN → фича выключена: не шлёт, не падает."""
    from core import bot_notify
    _CaptureClient.captured = {}
    with patch.object(bot_notify.config, "log_bot_token", ""), \
         patch.object(bot_notify.config, "log_chat_id", "-100"), \
         patch.object(bot_notify.httpx, "AsyncClient", _CaptureClient):
        ok = await bot_notify.notify_log_group("x", thread_id="1")
    assert ok is False
    assert _CaptureClient.captured == {}  # POST не вызывался


@pytest.mark.asyncio
async def test_notify_log_group_disabled_when_no_chat():
    from core import bot_notify
    with patch.object(bot_notify.config, "log_bot_token", "t"), \
         patch.object(bot_notify.config, "log_chat_id", ""):
        ok = await bot_notify.notify_log_group("x")
    assert ok is False


@pytest.mark.asyncio
async def test_notify_log_group_swallows_network_error():
    from core import bot_notify
    with patch.object(bot_notify.config, "log_bot_token", "t"), \
         patch.object(bot_notify.config, "log_chat_id", "-1"), \
         patch.object(bot_notify.httpx, "AsyncClient", _BoomClient):
        ok = await bot_notify.notify_log_group("x", thread_id="1")
    assert ok is False


# ── log_error → роутинг топиков по bot_label ─────────────────────────────────

@pytest.mark.asyncio
async def test_log_error_routes_by_bot_label():
    from core import error_log, bot_notify
    calls: list = []

    async def _fake_send(text, thread_id=""):
        calls.append({"text": text, "thread": thread_id})
        return True

    with patch.object(error_log.config, "log_thread_nexus", "111"), \
         patch.object(error_log.config, "log_thread_arcana", "222"), \
         patch.object(bot_notify, "notify_log_group", _fake_send):
        await error_log.log_error("boom-n", "processing_error", bot_label="☀️ Nexus")
        await error_log.log_error("boom-a", "parse_error", bot_label="🌒 Arcana")

    assert calls[0]["thread"] == "111"          # Nexus → свой топик
    assert "Nexus" in calls[0]["text"]
    assert "boom-n" in calls[0]["text"]
    assert calls[1]["thread"] == "222"          # Arcana → свой топик
    assert "Arcana" in calls[1]["text"]


@pytest.mark.asyncio
async def test_log_error_html_escapes_payload():
    """Пользовательский текст экранируется — не ломает parse_mode=HTML."""
    from core import error_log, bot_notify
    sent: dict = {}

    async def _fake_send(text, thread_id=""):
        sent["text"] = text
        return True

    with patch.object(bot_notify, "notify_log_group", _fake_send):
        await error_log.log_error("<script>x</script>", "processing_error")
    assert "<script>" not in sent["text"]
    assert "&lt;script&gt;" in sent["text"]


@pytest.mark.asyncio
async def test_log_error_never_raises_on_send_failure():
    """Сбой зеркалирования в группу не должен валить log_error."""
    from core import error_log, bot_notify

    async def _boom(text, thread_id=""):
        raise RuntimeError("send failed")

    with patch.object(bot_notify, "notify_log_group", _boom):
        ok = await error_log.log_error("boom", "processing_error")
    assert ok is True  # log_error всё равно вернул True
