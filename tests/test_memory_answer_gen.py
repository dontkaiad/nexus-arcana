"""tests/test_memory_answer_gen.py — ответ своими словами (core/memory_answer.py): вход, grounding, fallback, журнал.

LLM замокан. Журнал memory_answer_log — реальный Postgres (DATABASE_URL, миграции до head).
Privacy: вымышленные имена и факты.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from core import memory_answer as ma
from core.db import get_engine
from core.memory_search import Group, Hit, Result
from core.repos.pg_memory_repo import Memory


def mem(i, fact, plat="tg", date="2024-05-01", quote="", source="auto", person=5):
    return Memory(id=str(i), fact=fact, source_platform=plat, source_message_date=date, source_quote=quote,
                  source=source, person_id=person, date=date)


def result(groups, types=("dislikes",), route="direct", kind="facts", subject="person"):
    gs = [Group(label=lbl, person_id=pid, hits=[Hit(memory_id=int(m.id), score=0.0, memory=m) for m in ms])
          for lbl, pid, ms in groups]
    return Result(route=route, kind=kind, subject=subject, types=tuple(types), groups=gs)


MISHA = [mem(1, "не любит спешку и смену планов"), mem(2, "не любит очереди"),
         mem(3, "не любит, когда его считают тупым", plat="vk", date="2025-02-10")]


@pytest.fixture
def res():
    return result([("Миша Ари", 67, MISHA)])


@pytest.fixture(autouse=True)
def _gen_on(monkeypatch):
    monkeypatch.setenv("MEMORY_GENERATE_ANSWER", "1")
    monkeypatch.delenv("MEMORY_ANSWER_MODEL", raising=False)
    with get_engine().connect() as c:
        last = c.execute(text("SELECT coalesce(max(id), 0) FROM memory_answer_log")).scalar()
    yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM memory_answer_log WHERE id > :i"), {"i": last})


GOOD = {"blocks": [{"group": 1, "lines": [
    {"t": "спешку и смену планов", "ids": [1]}, {"t": "очереди", "ids": [2]},
    {"t": "когда его считают тупым", "ids": [3]}]}]}


def gen(res, raw, **kw):
    ask = AsyncMock(return_value=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
    with patch.object(ma, "ask_claude", ask):
        out = asyncio.run(ma.generate(res, "что не любит миша", **kw))
    return out, ask


# ── вход ─────────────────────────────────────────────────────────────────────

def test_eligibility(res):
    assert ma.eligible(res)
    one = result([("Миша", 1, MISHA[:1])])
    assert not ma.eligible(one)                                   # один факт — как есть, без LLM
    assert not ma.eligible(result([("Миша", 1, MISHA)], route="birthday", kind="birthday"))
    assert not ma.eligible(result([("Миша", 1, MISHA)], route="unknown_person", kind="unknown_person"))
    assert not ma.eligible(result([("Миша", 1, MISHA)], kind="none"))
    assert ma.eligible(result([("", None, MISHA)], route="hybrid_self", subject="self"))


def test_flag_off_disables(monkeypatch, res):
    monkeypatch.setenv("MEMORY_GENERATE_ANSWER", "0")
    assert not ma.eligible(res)


def test_build_groups_limits_and_prompt():
    many = [mem(i, f"факт номер {i}", quote="цитата " * 80) for i in range(1, 60)]
    r = result([("Первый", 1, many), ("Второй", 2, many[:30])])
    groups = ma.build_groups(r)
    assert sum(len(g.facts) for g in groups) == ma.MAX_FACTS and len(groups[0].facts) == ma.MAX_PER_GROUP
    assert all(len(f.quote) <= ma.QUOTE_LEN for g in groups for f in g.facts)
    p = ma.build_prompt("вопрос", groups, ("dislikes",))
    assert "[1] Первый" in p and "[2] Второй" in p and "id=1 |" in p and "что не любит" in p


def test_prompt_contains_only_facts_of_result(res):
    p = ma.build_prompt("что не любит миша", ma.build_groups(res), res.types)
    for m in MISHA:
        assert m.fact in p
    assert "Миша Ари" in p


# ── успех ────────────────────────────────────────────────────────────────────

def test_success_assembles_grouped_text_and_source_line(res):
    out, ask = gen(res, GOOD)
    assert out.ok and out.used_ids == [1, 2, 3]
    assert out.text.startswith("<b>Миша Ари</b> не любит: спешку и смену планов, очереди, когда его считают тупым.")
    assert "по фактам из TG/VK, 2024–2025" in out.text
    kw = ask.await_args.kwargs
    assert kw["model"] == "claude-haiku-5-5" and kw["thinking"] == "off"
    assert "Бот — Nexus" in kw["system"]


def test_arcana_voice_and_model_rollback_by_env(monkeypatch, res):
    monkeypatch.setenv("MEMORY_ANSWER_MODEL", "claude-haiku-4-5-20251001")
    out, ask = gen(res, GOOD, arcana=True)
    kw = ask.await_args.kwargs
    assert out.ok and kw["model"] == "claude-haiku-4-5-20251001" and "Бот — Arcana" in kw["system"]


def test_homonyms_separate_paragraphs_with_labels():
    a, b = MISHA[:2], [mem(11, "не любит жару"), mem(12, "не любит шум")]
    r = result([("Михаил Тестов (коллега)", 1, a), ("Миша Образцов", 2, b)])
    raw = {"blocks": [{"group": 1, "lines": [{"t": "спешку", "ids": [1]}, {"t": "очереди", "ids": [2]}]},
                      {"group": 2, "lines": [{"t": "жару и шум", "ids": [11, 12]}]}]}
    out, _ = gen(r, raw)
    assert out.ok
    paras = out.text.split("\n\n")
    assert paras[0].startswith("<b>Михаил Тестов (коллега)</b> не любит: спешку, очереди.")
    assert paras[1].startswith("<b>Миша Образцов</b> не любит: жару и шум.")


def test_self_route_no_header_capitalized():
    r = result([("", None, [mem(21, "не ем сладкое", person=None), mem(22, "не ем рыбу", person=None)])],
               types=("dislikes",), route="hybrid_self", subject="self")
    out, _ = gen(r, {"blocks": [{"group": 1, "lines": [{"t": "сладкое", "ids": [21]}, {"t": "рыбу", "ids": [22]}]}]})
    assert out.ok and out.text.startswith("Сладкое, рыбу.")


def test_markdown_fenced_json_ok(res):
    out, _ = gen(res, "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```")
    assert out.ok


def test_html_is_escaped():
    r = result([("<b>X</b>", 1, [mem(31, "не любит a < b"), mem(32, "не любит c & d")])])
    out, _ = gen(r, {"blocks": [{"group": 1, "lines": [{"t": "a < b", "ids": [31]}, {"t": "c & d", "ids": [32]}]}]})
    assert out.ok and "&lt;b&gt;X&lt;/b&gt;" in out.text and "a &lt; b" in out.text and "c &amp; d" in out.text


# ── grounding: каждый случай → отброшено (ok=False) с номером проверки ───────

def _bad(res, blocks, check, bad_ids=None):
    out, _ = gen(res, {"blocks": blocks})
    assert not out.ok and out.check == check and out.reason == f"check_{check}"
    if bad_ids is not None:
        assert out.bad_ids == bad_ids
    return out


def test_check2_foreign_id_dropped(res):
    _bad(res, [{"group": 1, "lines": [{"t": "очереди", "ids": [2, 999]}]}], "2", [999])


def test_check2_id_from_other_group_dropped():
    r = result([("А", 1, MISHA[:2]), ("Б", 2, [mem(11, "не любит жару"), mem(12, "не любит шум")])])
    _bad(r, [{"group": 1, "lines": [{"t": "жару", "ids": [11]}]}], "2", [11])        # тёзок не смешиваем


def test_check3_line_without_ids(res):
    _bad(res, [{"group": 1, "lines": [{"t": "очереди", "ids": []}]}], "3")


def test_check4a_invented_number(res):
    _bad(res, [{"group": 1, "lines": [{"t": "очереди дольше 20 минут", "ids": [2]}]}], "4a", [2])


def test_check4b_invented_name(res):
    _bad(res, [{"group": 1, "lines": [{"t": "очереди как у Ивана", "ids": [2]}]}], "4b", [2])


def test_check4c_invented_concept(res):
    _bad(res, [{"group": 1, "lines": [{"t": "очереди и чрезмерную критику", "ids": [2]}]}], "4c", [2])


def test_check5_limits(res):
    many = [{"t": "очереди", "ids": [2]}] * (ma.MAX_LINES_PER_BLOCK + 1)
    _bad(res, [{"group": 1, "lines": many}], "5")
    long_line = {"t": "очереди " * 150, "ids": [2]}
    _bad(res, [{"group": 1, "lines": [long_line]}], "5")


def test_check6_empty_blocks_do_not_invent(res):
    _bad(res, [], "6")


@pytest.mark.parametrize("raw", [
    "не JSON", "", '{"blocks": "x"}', '[{"group":1}]', '{"blocks":[{"group":"1","lines":[]}]}',
    '{"blocks":[{"group":7,"lines":[{"t":"очереди","ids":[2]}]}]}',
    '{"blocks":[{"group":1,"lines":[{"t":"очереди","ids":["2"]}]}]}',
])
def test_malformed_output_falls_back(res, raw):
    out, _ = gen(res, raw)
    assert not out.ok and out.text == ""


def test_timeout_falls_back(monkeypatch, res):
    async def slow(**kw):
        await asyncio.sleep(1)
        return json.dumps(GOOD)
    monkeypatch.setattr(ma, "GEN_TIMEOUT", 0.05)
    with patch.object(ma, "ask_claude", slow):
        out = asyncio.run(ma.generate(res, "q"))
    assert not out.ok and out.reason == "timeout"


def test_api_error_empty_answer_falls_back(res):
    out, _ = gen(res, "")
    assert not out.ok and out.reason == "api_error"


def test_exception_in_llm_call_falls_back(res):
    with patch.object(ma, "ask_claude", AsyncMock(side_effect=RuntimeError("boom"))):
        out = asyncio.run(ma.generate(res, "q"))
    assert not out.ok and out.reason == "api_error"


def test_less_than_two_facts_never_calls_llm():
    one = result([("Миша", 1, MISHA[:1])])
    out, ask = gen(one, GOOD)
    assert not out.ok and out.reason == "skipped"
    ask.assert_not_called()


# ── источник ─────────────────────────────────────────────────────────────────

def test_source_line_variants():
    g = lambda *fs: ma.build_groups(result([("X", 1, list(fs))]))
    assert ma.source_line(g(mem(1, "a", "tg", "2024-01-01")), [1]) == "<i>по фактам из TG, 2024</i>"
    assert "TG/VK, 2024–2025" in ma.source_line(g(mem(1, "a", "tg", "2024-01-01"), mem(2, "b", "vk", "2025-03-03")), [1, 2])
    manual = mem(3, "c", plat="", date="2026-10-01", source="manual")
    assert ma.source_line(g(manual), [3]) == "<i>добавлено вручную, 2026</i>"


# ── журнал и счётчик ─────────────────────────────────────────────────────────

def _rows(route):
    with get_engine().connect() as c:
        return c.execute(text("SELECT outcome, reason, fact_ids, n_facts, model FROM memory_answer_log "
                              "WHERE route = :r ORDER BY id"), {"r": route}).all()


def test_every_attempt_is_logged_with_reason_and_ids(res):
    res.route = "test_log"
    gen(res, GOOD)
    gen(res, {"blocks": [{"group": 1, "lines": [{"t": "очереди", "ids": [2, 999]}]}]})
    gen(res, {"blocks": [{"group": 1, "lines": [{"t": "очереди 5 раз", "ids": [2]}]}]})
    ok, fb2, fb4 = _rows("test_log")
    assert ok.outcome == "ok" and ok.fact_ids == [1, 2, 3] and ok.model == "claude-haiku-5-5"
    assert fb2.outcome == "fallback" and fb2.reason == "check_2" and fb2.fact_ids == [999]
    assert fb4.reason == "check_4a" and fb4.fact_ids == [2] and fb4.n_facts == 3


def test_fallback_is_logged_to_logger_with_check_and_ids(res, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="core.memory_answer"):
        gen(res, {"blocks": [{"group": 1, "lines": [{"t": "очереди", "ids": [2, 999]}]}]})
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "fallback" in msg and "check=2" in msg and "999" in msg


def test_stats_script_counts_and_reasons(capsys, res):
    res.route = "test_stats"
    gen(res, GOOD)
    gen(res, "не JSON")
    from importlib import util
    from pathlib import Path
    spec = util.spec_from_file_location("_stats", Path(__file__).resolve().parent.parent / "scripts" / "memory_answer_stats.py")
    mod = util.module_from_spec(spec); spec.loader.exec_module(mod)
    with patch("sys.argv", ["x", "--days", "1"]):
        assert mod.main() == 0
    out = capsys.readouterr().out
    assert "fallback" in out and "check_1" in out


def test_trace_hooks_called(res):
    steps = []
    gen(res, GOOD, trace=lambda s, d: steps.append(s))
    gen(res, "x", trace=lambda s, d: steps.append(s))
    assert steps == ["gen_input", "gen_output", "gen_input", "gen_fallback"]
