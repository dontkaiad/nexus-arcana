"""Resilience-обёртка LLM-вызовов (#91).

core/claude_client.py: retry_transient — экспоненциальный backoff + jitter,
ретраятся ТОЛЬКО транзиентные ошибки (429 с уважением Retry-After, 5xx,
timeout, connection). 4xx кроме 429 — без ретрая. После исчерпания
MAX_ATTEMPTS — graceful fallback (""/{}/None).

core/voice.py: тот же паттерн для OpenAI Whisper (aiohttp).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import pytest

import core.claude_client as cc
import core.voice as voice


# ── фабрики ошибок Anthropic ──────────────────────────────────────────────────

_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _err_429(retry_after=None):
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    resp = httpx.Response(429, headers=headers, request=_REQ)
    return anthropic.RateLimitError("rate limited", response=resp, body=None)


def _err_500():
    return anthropic.InternalServerError(
        "overloaded", response=httpx.Response(500, request=_REQ), body=None
    )


def _err_400():
    return anthropic.BadRequestError(
        "bad request", response=httpx.Response(400, request=_REQ), body=None
    )


def _err_timeout():
    return anthropic.APITimeoutError(request=_REQ)


def _err_connection():
    return anthropic.APIConnectionError(request=_REQ)


def _ok_response(text="ответ"):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _patch_client(side_effect):
    """Подменяет get_anthropic стабом; возвращает AsyncMock messages.create."""
    create = AsyncMock(side_effect=side_effect)
    stub = SimpleNamespace(messages=SimpleNamespace(create=create))
    return patch.object(cc, "get_anthropic", return_value=stub), create


def _patch_sleep():
    """Без реальных пауз; записывает запрошенные задержки."""
    return patch.object(cc.asyncio, "sleep", new=AsyncMock())


# ── retry_transient: транзиентные ошибки ретраятся ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("err_factory", [_err_429, _err_500, _err_timeout, _err_connection])
async def test_transient_error_then_success(err_factory):
    """Транзиентная ошибка → ретрай → успех."""
    patcher, create = _patch_client([err_factory(), _ok_response("ок")])
    with patcher, _patch_sleep():
        result = await cc.ask_claude("привет")
    assert result == "ок"
    assert create.call_count == 2


@pytest.mark.asyncio
async def test_429_respects_retry_after():
    """429 с Retry-After → пауза берётся из заголовка, не из backoff."""
    patcher, create = _patch_client([_err_429(retry_after=7), _ok_response()])
    sleep = AsyncMock()
    with patcher, patch.object(cc.asyncio, "sleep", new=sleep):
        result = await cc.ask_claude("привет")
    assert result == "ответ"
    sleep.assert_awaited_once_with(7.0)


@pytest.mark.asyncio
async def test_exhaustion_returns_empty_string():
    """Исчерпание MAX_ATTEMPTS → graceful fallback ask_claude ("")."""
    patcher, create = _patch_client([_err_429()] * cc.MAX_ATTEMPTS)
    with patcher, _patch_sleep():
        result = await cc.ask_claude("привет")
    assert result == ""
    assert create.call_count == cc.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_4xx_not_retried():
    """400 — не транзиентная, без ретрая, сразу fallback."""
    patcher, create = _patch_client([_err_400()])
    sleep = AsyncMock()
    with patcher, patch.object(cc.asyncio, "sleep", new=sleep):
        result = await cc.ask_claude("привет")
    assert result == ""
    assert create.call_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_covered_by_retry():
    """ask_claude_vision идёт через тот же _create_message."""
    patcher, create = _patch_client([_err_500(), _ok_response("видение")])
    with patcher, _patch_sleep():
        result = await cc.ask_claude_vision("что на фото?", image_b64="aGk=")
    assert result == "видение"
    assert create.call_count == 2


@pytest.mark.asyncio
async def test_parser_fallback_empty_dict_on_exhaustion():
    """Парсеры после исчерпания получают "" от ask_claude → {}."""
    patcher, _ = _patch_client([_err_429()] * cc.MAX_ATTEMPTS)
    with patcher, _patch_sleep():
        result = await cc.parse_finance("любой текст")
    assert result == {}


def test_backoff_is_exponential_with_jitter():
    delays = [cc.backoff_delay(a) for a in range(3)]
    assert 1.0 <= delays[0] <= 2.0
    assert 2.0 <= delays[1] <= 3.0
    assert 4.0 <= delays[2] <= 5.0


def test_sdk_builtin_retries_disabled():
    """max_retries=0 — иначе ретраи SDK множатся с нашими."""
    with patch.object(cc, "_client", None), \
            patch.object(cc.anthropic, "AsyncAnthropic") as ctor:
        cc.get_anthropic()
    kwargs = ctor.call_args.kwargs
    assert kwargs["max_retries"] == 0
    assert kwargs["timeout"] == cc.REQUEST_TIMEOUT


# ── voice.py: Whisper с тем же паттерном ──────────────────────────────────────

class _FakeResp:
    def __init__(self, status, json_data=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self._json = json_data or {}

    async def json(self):
        return self._json

    async def text(self):
        return "error body"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _patch_voice(responses, monkeypatch):
    session = _FakeSession(responses)
    monkeypatch.setattr(voice.aiohttp, "ClientSession", lambda **kw: session)
    monkeypatch.setattr(voice.config, "openai_key", "test-key")
    monkeypatch.setattr(voice.asyncio, "sleep", AsyncMock())
    return session


@pytest.mark.asyncio
async def test_whisper_429_then_success(monkeypatch):
    session = _patch_voice(
        [_FakeResp(429), _FakeResp(200, json_data={"text": " привет "})],
        monkeypatch,
    )
    result = await voice.transcribe(b"audio")
    assert result == "привет"
    assert session.calls == 2


@pytest.mark.asyncio
async def test_whisper_5xx_exhaustion_returns_none(monkeypatch):
    session = _patch_voice([_FakeResp(503)] * voice.MAX_ATTEMPTS, monkeypatch)
    result = await voice.transcribe(b"audio")
    assert result is None
    assert session.calls == voice.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_whisper_4xx_not_retried(monkeypatch):
    session = _patch_voice([_FakeResp(401)], monkeypatch)
    result = await voice.transcribe(b"audio")
    assert result is None
    assert session.calls == 1


@pytest.mark.asyncio
async def test_whisper_respects_retry_after(monkeypatch):
    _patch_voice(
        [_FakeResp(429, headers={"Retry-After": "5"}), _FakeResp(200, json_data={"text": "ок"})],
        monkeypatch,
    )
    sleep = AsyncMock()
    monkeypatch.setattr(voice.asyncio, "sleep", sleep)
    result = await voice.transcribe(b"audio")
    assert result == "ок"
    sleep.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
async def test_whisper_connection_error_exhaustion_returns_none(monkeypatch):
    import aiohttp as _aiohttp

    class _RaisingSession(_FakeSession):
        def post(self, *args, **kwargs):
            self.calls += 1
            raise _aiohttp.ClientError("boom")

    session = _RaisingSession([])
    monkeypatch.setattr(voice.aiohttp, "ClientSession", lambda **kw: session)
    monkeypatch.setattr(voice.config, "openai_key", "test-key")
    monkeypatch.setattr(voice.asyncio, "sleep", AsyncMock())
    result = await voice.transcribe(b"audio")
    assert result is None
    assert session.calls == voice.MAX_ATTEMPTS
