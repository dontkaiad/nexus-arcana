"""tests/test_llm_health.py — «ИИ недоступен»: классификация ошибок, дроссель уведомлений (30 мин), ответ пользователю,
изоляция ключа tools/evals. Без сети."""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import pytest

from core import claude_client as cc
from core import llm_health as lh
from core import pending_kv


def _api_error(cls, status, msg):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req, json={"type": "error", "error": {"message": msg}})
    return cls(msg, response=resp, body={"error": {"message": msg}})


BILLING = _api_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API.")


@pytest.fixture(autouse=True)
def _clean():
    lh._state.update(kind=None, since=0.0)
    pending_kv.delete(lh._KV_UID, lh._KV_KIND)
    yield
    lh._state.update(kind=None, since=0.0)
    pending_kv.delete(lh._KV_UID, lh._KV_KIND)


def test_classify_error():
    assert lh.classify_error(BILLING) == "billing"
    assert lh.classify_error(_api_error(anthropic.AuthenticationError, 401, "invalid x-api-key")) == "auth"
    assert lh.classify_error(_api_error(anthropic.PermissionDeniedError, 403, "forbidden")) == "auth"
    assert lh.classify_error(_api_error(anthropic.BadRequestError, 400, "max_tokens too large")) is None   # обычный 400 — не наш случай
    assert lh.classify_error(_api_error(anthropic.RateLimitError, 429, "slow down")) is None


def test_alert_throttled_to_once_per_30_min():
    sent = AsyncMock()
    with patch.object(lh, "_send_alert", sent):
        async def run():
            lh.on_error(BILLING)
            lh.on_error(BILLING)
            lh.on_error(BILLING)
            await asyncio.sleep(0)
        asyncio.run(run())
    assert sent.await_count == 1 and lh.unavailable() == "billing"


def test_alert_goes_to_every_allowed_account():
    calls = []

    async def fake_notify(tg_id, text, bot="nexus"):
        calls.append((tg_id, bot, "баланс" in text))
        return True
    cfg = SimpleNamespace(allowed_ids=[111, 222])
    with patch("core.bot_notify.notify_user", fake_notify), patch("core.config.config", cfg):
        asyncio.run(lh._send_alert("billing"))
    assert calls == [(111, "nexus", True), (222, "nexus", True)]


def test_ok_call_clears_state():
    lh._state.update(kind="billing", since=1.0)
    lh.on_ok()
    assert lh.unavailable() is None


def test_ask_claude_reports_billing_and_returns_empty():
    with patch.object(cc, "_create_message", AsyncMock(side_effect=BILLING)), patch.object(lh, "_send_alert", AsyncMock()):
        async def run():
            lh.begin_update()
            out = await cc.ask_claude("p", role="parse", temperature=0)
            return out, lh.hit_in_update()
        out, hit = asyncio.run(run())
    assert out == "" and hit and lh.unavailable() == "billing"


def test_middleware_answers_instead_of_silence():
    from core.middleware import LlmNoticeMiddleware
    event = SimpleNamespace(message=None, answer=AsyncMock())

    async def handler(ev, data):
        lh._hit.set(True)                      # во время обработки ИИ оказался недоступен
        return "res"
    out = asyncio.run(LlmNoticeMiddleware()(handler, event, {}))
    assert out == "res"
    event.answer.assert_awaited_once_with("🧠 ИИ временно недоступен, работают кнопки и Mini App")


def test_middleware_callback_answers_in_its_message():
    from core.middleware import LlmNoticeMiddleware
    inner = SimpleNamespace(answer=AsyncMock())
    event = SimpleNamespace(message=inner, answer=AsyncMock())

    async def handler(ev, data):
        lh._hit.set(True)
        return None
    asyncio.run(LlmNoticeMiddleware()(handler, event, {}))
    inner.answer.assert_awaited_once()
    event.answer.assert_not_awaited()                              # не тост callback'а, а сообщение в чат


def test_middleware_silent_when_llm_fine():
    from core.middleware import LlmNoticeMiddleware
    event = SimpleNamespace(message=None, answer=AsyncMock())

    async def handler(ev, data):
        return 1
    asyncio.run(LlmNoticeMiddleware()(handler, event, {}))
    event.answer.assert_not_awaited()


# ── ключ tools/evals ─────────────────────────────────────────────────────────

def test_tools_scope_refuses_without_tools_key(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_KEY_SCOPE", "tools")
    monkeypatch.delenv("ANTHROPIC_API_KEY_TOOLS", raising=False)
    monkeypatch.chdir(tmp_path)                              # без .env рядом
    monkeypatch.setattr(cc, "_client", None)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY_TOOLS"):
        cc.get_anthropic()


def test_tools_scope_uses_tools_key_never_main(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_KEY_SCOPE", "tools")
    monkeypatch.setenv("ANTHROPIC_API_KEY_TOOLS", "sk-tools")
    monkeypatch.setattr(cc, "_client", None)
    assert cc._api_key() == "sk-tools"
    monkeypatch.delenv("ANTHROPIC_API_KEY_TOOLS")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):                         # основной ключ остаётся недоступным как запасной
        cc._api_key()


def test_bot_scope_uses_main_key(monkeypatch):
    monkeypatch.delenv("LLM_KEY_SCOPE", raising=False)
    assert cc._api_key() == cc.config.anthropic_key


def test_evals_and_llm_scripts_pin_tools_scope():
    import os
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for rel in ("evals/__init__.py", "tools/people_import/extract_facts.py", "scripts/memory_search_eval.py",
                "scripts/assign_memory_subjects.py"):
        assert 'LLM_KEY_SCOPE", "tools"' in (root / rel).read_text(encoding="utf-8"), rel
