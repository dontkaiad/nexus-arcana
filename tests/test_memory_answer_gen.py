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
    {"t": "Планы", "n": "спешка, смена планов", "ids": [1]}, {"t": "Очереди", "n": "не любит очереди", "ids": [2]},
    {"t": "Оценка", "n": "считают тупым", "ids": [3]}]}]}


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

def test_success_assembles_bullets_and_source_line(res):
    out, ask = gen(res, GOOD)
    assert out.ok and out.used_ids == [1, 2, 3]
    head, *bullets = out.text.split("\n\n")[0].split("\n")
    assert head == "<b>Миша Ари</b> не любит:"
    assert bullets == ["• <b>Планы</b> — спешка, смена планов", "• <b>Очереди</b> — не любит очереди",
                       "• <b>Оценка</b> — считают тупым"]
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
    raw = {"blocks": [{"group": 1, "lines": [{"t": "Спешка", "n": "не любит спешку", "ids": [1]},
                                             {"t": "Очереди", "n": "не любит очереди", "ids": [2]}]},
                      {"group": 2, "lines": [{"t": "Среда", "n": "жара, шум", "ids": [11, 12]}]}]}
    out, _ = gen(r, raw)
    assert out.ok
    paras = out.text.split("\n\n")
    assert paras[0].startswith("<b>Михаил Тестов (коллега)</b> не любит:\n• <b>Спешка</b>")
    assert paras[1].startswith("<b>Миша Образцов</b> не любит:\n• <b>Среда</b> — жара, шум")


# ── тёзки всегда оба (регрессия: блок Михаила пропал) ────────────────────────

def _two_groups():
    a = [mem(1, "не любит спешку"), mem(2, "не любит очереди")]
    b = [mem(11, "не любит жару"), mem(12, "не любит шум")]
    return result([("Михаил Тестов", 1, a), ("Миша Образцов", 2, b)])


def test_homonym_whose_lines_all_fail_check_still_appears_via_deterministic_line():
    raw = {"blocks": [
        {"group": 1, "lines": [{"t": "Спешка", "n": "выдуманное понятие", "ids": [1]}]},      # 4c → отброшено
        {"group": 2, "lines": [{"t": "Среда", "n": "жара, шум", "ids": [11, 12]}]}]}
    out, _ = gen(_two_groups(), raw)
    assert out.ok
    p1, p2 = out.text.split("\n\n")[:2]
    assert p1.startswith("<b>Михаил Тестов</b> не любит:") and "• не любит спешку" in p1 and "• не любит очереди" in p1
    assert p2.startswith("<b>Миша Образцов</b> не любит:\n• <b>Среда</b>")
    assert out.used_ids == [1, 2, 11, 12] and out.reason.startswith("dropped:4c")


def test_homonym_missing_from_model_answer_still_appears():
    raw = {"blocks": [{"group": 2, "lines": [{"t": "Среда", "n": "жара, шум", "ids": [11, 12]}]}]}
    out, _ = gen(_two_groups(), raw)
    assert out.ok and "<b>Михаил Тестов</b>" in out.text and "• не любит спешку" in out.text


def test_first_group_cannot_eat_the_second_groups_budget():
    big = [mem(100 + i, f"не любит предмет{chr(97 + i)}{chr(97 + i)}{chr(97 + i)}") for i in range(8)]
    r = result([("Первый", 1, big), ("Второй", 2, [mem(200, "не любит жару"), mem(201, "не любит шум")])])
    lines = [{"t": f"Тема{chr(97 + i)}", "n": "не любит предмет", "ids": [100 + i]} for i in range(8)]
    out, _ = gen(r, {"blocks": [{"group": 1, "lines": lines},
                                {"group": 2, "lines": [{"t": "Среда", "n": "жара, шум", "ids": [200, 201]}]}]})
    p1, p2 = out.text.split("\n\n")[:2]
    assert p1.count("• ") == ma.KEEP_LINES and p2.startswith("<b>Второй</b>") and "• <b>Среда</b>" in p2


def test_self_route_no_header():
    r = result([("", None, [mem(21, "не ем сладкое", person=None), mem(22, "не ем рыбу", person=None)])],
               types=("dislikes",), route="hybrid_self", subject="self")
    out, _ = gen(r, {"blocks": [{"group": 1, "lines": [{"t": "Еда", "n": "сладкое, рыбу", "ids": [21, 22]}]}]})
    assert out.ok and out.text.startswith("• <b>Еда</b> — сладкое, рыбу")


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
    _bad(res, [{"group": 1, "lines": [{"t": "Очереди", "n": "чрезмерную критику", "ids": [2]}]}], "4c", [2])
    # тема только называет кластер: её слова не обязаны быть в фактах
    out, _ = gen(res, {"blocks": [{"group": 1, "lines": [{"t": "Ожидание", "n": "не любит очереди", "ids": [2]}]}]})
    assert out.ok


def test_check5_absurd_format_only(res):
    _bad(res, [{"group": 1, "lines": [{"t": "очереди", "ids": [2]}] * (ma.MAX_LINES_PER_BLOCK + 1)}], "5")
    _bad(res, [{"group": 1, "lines": [{"t": "очереди " * 60, "ids": [2]}]}], "5")


def test_long_line_allowed_when_cited_facts_are_long():
    long_fact = "не ем " + "очень длинный пересказ факта " * 12            # ~350 символов
    r = result([("", None, [mem(41, long_fact, person=None), mem(42, "не ем рыбу", person=None)])],
               types=("dislikes",), route="hybrid_self", subject="self")
    line = long_fact[:330]
    out, _ = gen(r, {"blocks": [{"group": 1, "lines": [{"t": line, "ids": [41]}]}]})
    assert out.ok
    out2, _ = gen(r, {"blocks": [{"group": 1, "lines": [{"t": "рыбу " * 80, "ids": [42]}]}]})    # длиннее источника → нет
    assert not out2.ok and out2.check == "5"


def test_size_overflow_is_trimmed_not_dropped():
    facts = [mem(100 + i, f"не любит вещь{chr(97 + i)}{chr(97 + i)}{chr(97 + i)}") for i in range(10)]
    r = result([("Миша", 1, facts)])
    lines = [{"t": f"Тема{chr(97 + i)}", "n": f"вещь{chr(97 + i)}{chr(97 + i)}{chr(97 + i)}", "ids": [100 + i]} for i in range(10)]
    out, _ = gen(r, {"blocks": [{"group": 1, "lines": lines}]})
    assert out.ok
    assert out.used_ids == [100 + i for i in range(ma.KEEP_LINES)]          # показаны (и учтены) только оставленные
    assert out.text.split("\n\n")[0].count("• ") == ma.KEEP_LINES


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


# ── фразы без опоры отбрасываются по одной; один повтор при битом формате ────

def test_unsupported_line_dropped_good_lines_kept(res):
    raw = {"blocks": [{"group": 1, "lines": [
        {"t": "Планы", "n": "спешка, смена планов", "ids": [1]},
        {"t": "Критика", "n": "чрезмерную критику", "ids": [2]},          # 4c — выдумка
        {"t": "Время", "n": "дольше 20 минут", "ids": [2]},               # 4a — выдумка
        {"t": "Очереди", "n": "не любит очереди", "ids": [2]}]}]}
    out, _ = gen(res, raw)
    assert out.ok and out.used_ids == [1, 2]
    body = out.text.split("\n\n")[0]
    assert "<b>Планы</b>" in body and "<b>Очереди</b>" in body and "критику" not in body and "20" not in body
    assert out.reason == "dropped:4c,4a" and out.detail == "dropped=2"


def test_all_lines_unsupported_still_falls_back_with_first_check(res):
    out, _ = gen(res, {"blocks": [{"group": 1, "lines": [{"t": "Критика", "n": "чрезмерную критику", "ids": [2]},
                                                          {"t": "Время", "n": "дольше 20 минут", "ids": [2]}]}]})
    assert not out.ok and out.check == "4c"


def test_foreign_id_in_any_line_discards_everything_even_if_other_lines_fine(res):
    out, _ = gen(res, {"blocks": [{"group": 1, "lines": [{"t": "очереди", "ids": [2]},
                                                          {"t": "спешку", "ids": [1, 999]}]}]})
    assert not out.ok and out.check == "2" and out.bad_ids == [999]


def test_bad_format_retried_once_then_ok(res):
    ask = AsyncMock(side_effect=["не JSON", json.dumps(GOOD, ensure_ascii=False)])
    with patch.object(ma, "ask_claude", ask):
        out = asyncio.run(ma.generate(res, "q"))
    assert out.ok and ask.await_count == 2


def test_bad_format_twice_falls_back(res):
    ask = AsyncMock(return_value="не JSON")
    with patch.object(ma, "ask_claude", ask):
        out = asyncio.run(ma.generate(res, "q"))
    assert not out.ok and out.check == "1" and ask.await_count == 2


def test_grounding_violation_is_not_retried(res):
    ask = AsyncMock(return_value=json.dumps({"blocks": [{"group": 1, "lines": [{"t": "очереди", "ids": [2, 999]}]}]}))
    with patch.object(ma, "ask_claude", ask):
        out = asyncio.run(ma.generate(res, "q"))
    assert not out.ok and out.check == "2" and ask.await_count == 1


def test_timeout_is_not_retried(monkeypatch, res):
    async def slow(**kw):
        await asyncio.sleep(1)
    monkeypatch.setattr(ma, "GEN_TIMEOUT", 0.05)
    calls = []
    async def counting(**kw):
        calls.append(1)
        await slow(**kw)
    with patch.object(ma, "ask_claude", counting):
        out = asyncio.run(ma.generate(res, "q"))
    assert out.reason == "timeout" and len(calls) == 1
