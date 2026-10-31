"""tests/test_claude_client_gen5.py — единая обработка параметров моделей 5.x в core/claude_client.py.

Проверено на проде: Haiku 5.5 отвечает 400 на temperature («deprecated for this model»); мышление по умолчанию
включено (≈3 с на короткий JSON против ≈0.7 с с thinking disabled). Живых вызовов нет.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import claude_client as cc


def _kw(model, temperature=None, thinking=None):
    return cc.apply_model_params({"model": model}, model, temperature, thinking)


def test_gen5_detection():
    for m in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"):
        assert cc.is_gen5(m)
    for m in ("claude-haiku-4-5-20251001", "claude-sonnet-4-6", ""):
        assert not cc.is_gen5(m)


def test_gen5_never_sends_temperature():
    assert "temperature" not in _kw("claude-haiku-5-5", temperature=0)
    assert "temperature" not in _kw("claude-sonnet-5-5", temperature=0.7)


def test_gen4_keeps_temperature_and_ignores_thinking():
    kw = _kw("claude-haiku-4-5-20251001", temperature=0, thinking="off")
    assert kw["temperature"] == 0 and "extra_body" not in kw


@pytest.mark.parametrize("model,expect", [
    ("claude-haiku-5-5", {"thinking": {"type": "disabled"}}),
    ("claude-sonnet-5-5", {"thinking": {"type": "between_tools"}}),     # disabled там 400
    ("claude-opus-5-5", {"output_config": {"effort": "low"}}),           # не выключается — минимальный effort
])
def test_thinking_off_per_model_family(model, expect):
    assert _kw(model, thinking="off")["extra_body"] == expect


def test_thinking_low_and_default():
    assert _kw("claude-haiku-5-5", thinking="low")["extra_body"] == {"output_config": {"effort": "low"}}
    assert "extra_body" not in _kw("claude-haiku-5-5")


def test_first_text_skips_thinking_block():
    resp = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="ответ")])
    assert cc._first_text(resp) == "ответ"
    assert cc._first_text(SimpleNamespace(content=[])) == ""


def test_ask_claude_haiku55_request_shape_and_usage():
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")],
                           usage=SimpleNamespace(input_tokens=48, output_tokens=14))
    create = AsyncMock(return_value=resp)
    usage = {}
    with patch.object(cc, "_create_message", create):
        out = asyncio.run(cc.ask_claude("p", system="s", model="claude-haiku-5-5", temperature=0,
                                        thinking="off", usage_out=usage))
    kw = create.await_args.kwargs
    assert out == "{}" and "temperature" not in kw and kw["extra_body"] == {"thinking": {"type": "disabled"}}
    assert usage == {"input_tokens": 48, "output_tokens": 14}


def test_ask_claude_old_model_unchanged():
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="x")])
    create = AsyncMock(return_value=resp)
    with patch.object(cc, "_create_message", create):
        asyncio.run(cc.ask_claude("p", model="claude-haiku-4-5-20251001", temperature=0))
    kw = create.await_args.kwargs
    assert kw["temperature"] == 0 and "extra_body" not in kw
