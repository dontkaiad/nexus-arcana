"""tests/test_llm_usage.py — журнал вызовов LLM (токены / кеш / латентность) и prompt caching.

Живых вызовов нет. Таблица llm_call_log — реальный Postgres (миграции до head).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from core import claude_client as cc
from core import llm_usage
from core.db import get_engine


def _resp(text_="ok", i=100, o=20, cw=0, cr=0):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text_)],
                           usage=SimpleNamespace(input_tokens=i, output_tokens=o,
                                                 cache_creation_input_tokens=cw, cache_read_input_tokens=cr))


# ── кешируемый system ────────────────────────────────────────────────────────

def test_cacheable_system_variants():
    assert cc.cacheable_system("s", None) == "s"
    assert cc.cacheable_system("", "5m") == ""
    b = cc.cacheable_system("s", "5m")
    assert b == [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}]
    assert cc.cacheable_system("s", "1h")[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    blocks = [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": "b"}]
    assert cc.cacheable_system(blocks, "5m") is blocks                      # размеченные блоки не трогаем


def test_ask_claude_sends_cached_system_and_reports_cache_usage():
    create = AsyncMock(return_value=_resp(cw=900, cr=0))
    usage = {}
    with patch.object(cc, "_create_message", create), patch.object(llm_usage, "record") as rec:
        out = asyncio.run(cc.ask_claude("p", system="S" * 10, model="claude-haiku-4-5-20251001", cache_ttl="5m",
                                        usage_out=usage, label="t.cached"))
    assert out == "ok"
    sent = create.await_args.kwargs["system"]
    assert sent[0]["cache_control"] == {"type": "ephemeral"} and sent[0]["text"] == "S" * 10
    assert usage["cache_creation_input_tokens"] == 900 and usage["cache_read_input_tokens"] == 0
    args = rec.call_args.args
    assert args[0] == "t.cached" and args[1] == "claude-haiku-4-5-20251001" and args[2]["cache_creation_input_tokens"] == 900
    assert args[4] is True and args[5] == "5m"


def test_ask_claude_without_cache_keeps_plain_string_system():
    create = AsyncMock(return_value=_resp())
    with patch.object(cc, "_create_message", create), patch.object(llm_usage, "record"):
        asyncio.run(cc.ask_claude("p", system="plain"))
    assert create.await_args.kwargs["system"] == "plain"


def test_failed_call_is_logged_as_not_ok():
    import anthropic
    err = anthropic.APIConnectionError(request=SimpleNamespace())
    with patch.object(cc, "_create_message", AsyncMock(side_effect=err)), patch.object(llm_usage, "record") as rec:
        assert asyncio.run(cc.ask_claude("p")) == ""
    assert rec.call_args.args[4] is False


def test_label_defaults_to_caller_module_and_function():
    async def my_feature():
        return await cc.ask_claude("p")

    with patch.object(cc, "_create_message", AsyncMock(return_value=_resp())), patch.object(llm_usage, "record") as rec:
        asyncio.run(my_feature())
    assert rec.call_args.args[0].endswith(".my_feature") and "test_llm_usage" in rec.call_args.args[0]


# ── классификатор: статичный блок кешируется, позиции бюджета — хвостом ───────

def test_classifier_system_is_cached_static_block_plus_uncached_tail():
    from core import classifier

    captured = {}

    async def fake_ask(prompt, system="", **kw):
        captured["system"], captured["kw"] = system, kw
        return '[{"type":"unknown"}]'

    with patch.object(classifier, "ask_claude", fake_ask), \
         patch.object(classifier, "_known_budget_positions", AsyncMock(return_value=([{"name": "x", "amount": 1}], []))), \
         patch.object(classifier, "_budget_positions_prompt", return_value="ПОЗИЦИИ-БЮДЖЕТА"):
        asyncio.run(classifier.classify("какой-то текст без шаблонов 123 qwe", user_id="u"))
    sysm = captured["system"]
    assert isinstance(sysm, list) and len(sysm) == 2
    assert sysm[0]["cache_control"] == {"type": "ephemeral"} and len(sysm[0]["text"]) > 20000
    assert "cache_control" not in sysm[1] and "ПОЗИЦИИ-БЮДЖЕТА" in sysm[1]["text"]
    assert captured["kw"]["temperature"] == 0 and captured["kw"]["label"] == "classifier.classify"


def test_classifier_static_block_has_no_per_user_data():
    """Кеш общий для всех вызовов дня: в кешируемый блок не должно попадать ничего пользовательского."""
    from core import classifier
    a = classifier.build_system(3)
    assert classifier.build_system(3) == a                               # детерминирован при одной дате
    assert "ПОЗИЦИИ" not in a


# ── запись в журнал ──────────────────────────────────────────────────────────

@pytest.fixture
def logging_on(monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", "1")
    with get_engine().connect() as c:
        last = c.execute(text("SELECT coalesce(max(id), 0) FROM llm_call_log")).scalar()
    yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM llm_call_log WHERE id > :i"), {"i": last})


def test_record_writes_a_row_with_cache_fields(logging_on):
    llm_usage._write({"label": "t.row", "model": "claude-haiku-5-5", "i": 11, "cw": 22, "cr": 33, "o": 44,
                      "l": 555, "ok": True, "ttl": "5m", "th": "off"})
    with get_engine().connect() as c:
        r = c.execute(text("SELECT * FROM llm_call_log WHERE label = 't.row' ORDER BY id DESC LIMIT 1")).one()
    assert (r.in_tokens, r.cache_write_tokens, r.cache_read_tokens, r.out_tokens, r.latency_ms) == (11, 22, 33, 44, 555)
    assert r.ok is True and r.cache_ttl == "5m" and r.thinking == "off"


def test_record_in_running_loop_is_fire_and_forget(logging_on):
    async def go():
        llm_usage.record("t.async", "claude-haiku-5-5", {"input_tokens": 1}, 10, True)
        await asyncio.gather(*list(llm_usage._tasks))

    asyncio.run(go())
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM llm_call_log WHERE label = 't.async'")).scalar() == 1


def test_record_disabled_by_env(monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", "0")
    with patch.object(llm_usage, "_write") as w:
        llm_usage.record("t.off", "m", {}, 1, True)
    w.assert_not_called()


def test_record_never_raises_on_db_failure(logging_on):
    with patch("core.db.get_engine", side_effect=RuntimeError("db down")):
        llm_usage._write({"label": "x", "model": "m", "i": 0, "cw": 0, "cr": 0, "o": 0, "l": 0, "ok": True, "ttl": None, "th": None})


# ── сводка ───────────────────────────────────────────────────────────────────

def test_stats_price_and_cost_math():
    from importlib import util
    from pathlib import Path
    spec = util.spec_from_file_location("_stats", Path(__file__).resolve().parent.parent / "scripts" / "llm_usage_stats.py")
    m = util.module_from_spec(spec); spec.loader.exec_module(m)
    # Haiku 4.5: 10K вход обычный = $0.010; тот же вход ЧТЕНИЕМ из кеша = $0.001; запись 5m = $0.0125
    assert m.cost("claude-haiku-4-5-20251001", 10_000, 0, 0, 0) == pytest.approx(0.010)
    assert m.cost("claude-haiku-4-5-20251001", 0, 0, 10_000, 0) == pytest.approx(0.001)
    assert m.cost("claude-haiku-4-5-20251001", 0, 10_000, 0, 0) == pytest.approx(0.0125)
    assert m.cost("claude-haiku-4-5-20251001", 0, 10_000, 0, 0, 1.0) == pytest.approx(0.020)
    assert m.cost("claude-haiku-5-5", 1_000_000, 0, 0, 1_000_000) == pytest.approx(0.60)
