"""tests/test_claude_stream.py — core.claude_client.ask_claude_stream (#191).

Стриминговый примитив для SSE-эндпоинтов Mini App: yield'ит текстовые дельты
вместо возврата готовой строки (в отличие от ask_claude). Без retry_transient
(см. docstring функции) — на ошибке генератор просто останавливается, а не
кидает исключение молча повторно.
"""
from __future__ import annotations

from unittest.mock import patch

import anthropic
import httpx
import pytest

import core.claude_client as cc

_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _FakeAsyncTextStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


class _FakeStreamManager:
    """Имитирует AsyncMessageStreamManager: `async with client.messages.stream(...)`."""
    def __init__(self, chunks, *, raise_on_iter=None):
        self.chunks = chunks
        self.raise_on_iter = raise_on_iter
        self.captured_kwargs = None
        self.call_count = 0

    def __call__(self, **kwargs):
        self.call_count += 1
        self.captured_kwargs = kwargs
        return self

    async def __aenter__(self):
        if self.raise_on_iter:
            return _RaisingStream(self.raise_on_iter)
        return _FakeStream(self.chunks)

    async def __aexit__(self, *exc):
        return False


class _FakeStream:
    def __init__(self, chunks):
        self.text_stream = _FakeAsyncTextStream(chunks)


class _RaisingStream:
    def __init__(self, exc):
        self._exc = exc

    @property
    def text_stream(self):
        return self._gen()

    async def _gen(self):
        yield "частичный "
        raise self._exc


def _patch_client(stream_manager):
    stub_client = type("Stub", (), {"messages": type("M", (), {"stream": stream_manager})()})()
    return patch.object(cc, "get_anthropic", return_value=stub_client)


@pytest.mark.asyncio
async def test_yields_text_deltas_in_order():
    mgr = _FakeStreamManager(["Кар", "ты ", "лег", "ли."])
    with _patch_client(mgr):
        out = [chunk async for chunk in cc.ask_claude_stream("вопрос", system="сис")]
    assert out == ["Кар", "ты ", "лег", "ли."]


@pytest.mark.asyncio
async def test_passes_model_system_temperature_max_tokens():
    mgr = _FakeStreamManager(["ok"])
    with _patch_client(mgr):
        async for _ in cc.ask_claude_stream(
            "вопрос", system="сис", model="claude-sonnet-4-6",
            max_tokens=777, temperature=0.3,
        ):
            pass
    assert mgr.captured_kwargs["model"] == "claude-sonnet-4-6"
    assert mgr.captured_kwargs["system"] == "сис"
    assert mgr.captured_kwargs["max_tokens"] == 777
    assert mgr.captured_kwargs["temperature"] == 0.3
    assert mgr.captured_kwargs["messages"] == [{"role": "user", "content": "вопрос"}]


@pytest.mark.asyncio
async def test_defaults_to_haiku_model_when_none_given():
    mgr = _FakeStreamManager(["ok"])
    with _patch_client(mgr):
        async for _ in cc.ask_claude_stream("вопрос"):
            pass
    assert mgr.captured_kwargs["model"] == cc.config.model_haiku
    assert "system" not in mgr.captured_kwargs
    assert "temperature" not in mgr.captured_kwargs


@pytest.mark.asyncio
async def test_mid_stream_api_error_stops_generator_with_partial_output():
    """Сеть легла на середине — то, что успело прийти, отдано, дальше тихая
    остановка (без исключения наружу): ретрая тут нет намеренно, см. docstring."""
    err = anthropic.APIConnectionError(request=_REQ)
    mgr = _FakeStreamManager(None, raise_on_iter=err)
    out = []
    with _patch_client(mgr):
        async for chunk in cc.ask_claude_stream("вопрос"):
            out.append(chunk)
    assert out == ["частичный "]


@pytest.mark.asyncio
async def test_no_retry_only_one_stream_call_on_error():
    """В отличие от ask_claude/_create_message, здесь НЕТ retry_transient —
    один сетевой сбой не должен приводить к повторному вызову .stream()."""
    err = anthropic.APIConnectionError(request=_REQ)
    mgr = _FakeStreamManager(None, raise_on_iter=err)

    with _patch_client(mgr):
        async for _ in cc.ask_claude_stream("вопрос"):
            pass
    assert mgr.call_count == 1
