"""tests/test_memory_trace.py — трассировка вопросов к памяти: сбор строки из событий, запись в БД, вердикт trace_show.
Тексты фактов в трассу не попадают (только id); вымышленные данные."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from core import memory as mem
from core import memory_search as ms
from core.db import get_engine
from core.memory_trace import Tracer

MARK = "test-memory-trace"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM memory_trace WHERE user_id = :u"), {"u": MARK})


def _show_module():
    spec = importlib.util.spec_from_file_location("trace_show", REPO / "scripts" / "trace_show.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _hybrid_events(t):
    t("parse", {"subject": "self", "types": ["likes"], "intent": "facts", "people": [], "cats": []})
    t("legs", {"fts": [11, 12, 13], "vec": [12, 14], "vec_scores": [0.81234, 0.7]})
    t("rrf", {"ids": [12, 11, 14, 13]})
    t("rerank", {"ids": [12, 11]})
    t("result", {"route": "hybrid_self", "kind": "facts", "subject": "self", "types": ["likes"], "ids": [12, 11]})
    t("gen_input", {"ids": [12, 11], "groups": 1, "chars": 40})
    t("gen_output", {"ok": True, "used_ids": [12], "chars": 30})


def test_row_from_hybrid_chain_has_ranks_ids_and_steps():
    t = Tracer("nexus", MARK, "что я люблю")
    _hybrid_events(t)
    r = t.row()
    assert r["question"] == "что я люблю" and r["route"] == "hybrid_self" and r["subject"] == "self" and r["types"] == ["likes"]
    assert r["fts"] == [[11, 1], [12, 2], [13, 3]]
    assert r["vec"] == [[12, 1, 0.812], [14, 2, 0.7]]
    assert r["rrf_ids"] == [12, 11, 14, 13] and r["rerank"] == {"ran": True, "ids": [12, 11], "failed": None}
    assert r["final_ids"] == [12, 11] and r["gen"]["ok"] is True and r["gen"]["used_ids"] == [12]
    assert set(r["latency"]) >= {"parse", "legs", "rrf", "rerank", "result", "gen_input", "gen_output"}
    assert r["total_ms"] >= 0 and r["error"] is None
    assert not {"text", "fact_text", "facts"} & set(r)                                       # текстов фактов в строке нет, только id


def test_row_direct_unknown_person_rerank_failed_and_gen_fallback():
    t = Tracer("miniapp", MARK, "что любит Икс")
    t("parse", {"subject": "unknown_person", "types": [], "people": [], "cats": []})
    t("result", {"route": "unknown_person", "kind": "unknown_person", "subject": "unknown_person", "types": [], "ids": []})
    r = t.row()
    assert r["route"] == "unknown_person" and r["fts"] is None and r["rerank"] is None and r["gen"] is None

    t2 = Tracer("nexus", MARK, "q")
    t2("parse", {"subject": "unclear", "people": [], "cats": []})
    t2("legs", {"fts": [1], "vec": [], "vec_scores": []})
    t2("rrf", {"ids": [1]})
    t2("rerank_failed", {"reason": "JSONDecodeError"})
    t2("rerank", {"ids": [1]})
    t2("result", {"route": "hybrid", "kind": "facts", "subject": "unclear", "types": [], "ids": [1]})
    t2("gen_input", {"ids": [1, 2]})
    t2("gen_fallback", {"reason": "check_4", "check": "4", "ids": [2]})
    r2 = t2.row()
    assert r2["rerank"]["failed"] == "JSONDecodeError"
    assert r2["gen"] == {"attempted": True, "ok": False, "reason": "check_4", "check": "4", "used_ids": [], "bad_ids": [2]}


def test_finish_writes_one_row_and_swallows_errors():
    t = Tracer("nexus", MARK, "что я люблю")
    _hybrid_events(t)
    t.finish()
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT question, route, rrf_ids, final_ids, fts, gen FROM memory_trace WHERE user_id = :u"), {"u": MARK}).all()
    assert len(rows) == 1 and rows[0].route == "hybrid_self" and rows[0].final_ids == [12, 11] and rows[0].fts[0] == [11, 1]
    with patch("core.db.get_engine", side_effect=RuntimeError("db down")):
        Tracer("nexus", MARK, "q").finish()                              # не бросает


@pytest.mark.asyncio
async def test_search_memory_records_trace_even_when_empty():
    async def fake_answer(query, user_id="", scope="", k=5, do_rerank=True, trace=None):
        trace("parse", {"subject": "person", "types": ["likes"], "people": [7], "cats": []})
        trace("direct", {"ids": []})
        res = ms.Result(route="direct", kind="none", subject="person", types=("likes",), groups=[ms.Group(person_id=7)])
        trace("result", {"route": res.route, "kind": res.kind, "subject": res.subject, "types": ["likes"], "ids": []})
        return res

    msg = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=1), bot=AsyncMock(), chat=SimpleNamespace(id=1))
    with patch.object(mem._memory_search, "answer", fake_answer), patch.object(mem, "_search_finance", AsyncMock(return_value=[])):
        await mem.search_memory(msg, "что любит Оля", MARK, del_prefix="mem_del")
    with get_engine().connect() as c:
        r = c.execute(text("SELECT bot, question, route, kind, final_ids, parse FROM memory_trace WHERE user_id = :u"), {"u": MARK}).one()
    assert r.bot == "nexus" and r.question == "что любит Оля" and r.route == "direct" and r.kind == "none"
    assert r.parse["people"] == [7] and r.parse["direct_ids"] == []


@pytest.mark.asyncio
async def test_search_memory_records_error_name_not_text():
    async def boom(*a, **k):
        raise ValueError("секретный текст факта")

    msg = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=1), bot=AsyncMock(), chat=SimpleNamespace(id=1))
    with patch.object(mem._memory_search, "answer", boom), patch.object(mem, "_search_finance", AsyncMock(return_value=[])):
        with pytest.raises(ValueError):
            await mem.search_memory(msg, "вопрос", MARK, del_prefix="arcmem_del")
    with get_engine().connect() as c:
        r = c.execute(text("SELECT bot, error FROM memory_trace WHERE user_id = :u"), {"u": MARK}).one()
    assert r.bot == "arcana" and r.error == "ValueError"


@pytest.mark.parametrize("row,part", [
    ({"error": "TimeoutError"}, "исключение"),
    ({"route": "unknown_person"}, "имя не найдено"),
    ({"route": "direct", "final_ids": []}, "нет фактов этого типа"),
    ({"route": "hybrid", "fts": [], "vec": []}, "ни FTS, ни вектор"),
    ({"route": "hybrid", "fts": [[1, 1]], "vec": []}, "вектор: пусто"),
    ({"route": "hybrid", "fts": [[1, 1]], "vec": [[1, 1, 0.8]], "rerank": {"ran": True, "failed": "X", "ids": [1]}}, "реранк упал"),
    ({"route": "hybrid", "fts": [[1, 1]], "vec": [[1, 1, 0.8]], "rrf_ids": [1], "rerank": {"ran": True, "ids": []}, "final_ids": []}, "отсёк ВСЕХ"),
    ({"route": "hybrid", "fts": [[1, 1]], "vec": [[1, 1, 0.8]], "rrf_ids": [1], "rerank": {"ran": True, "ids": [1]}, "final_ids": [1],
      "gen": {"attempted": True, "ok": False, "reason": "check_4", "check": "4"}}, "fallback"),
    ({"route": "hybrid", "fts": [[1, 1]], "vec": [[1, 1, 0.8]], "rrf_ids": [1], "rerank": {"ran": True, "ids": [1]}, "final_ids": [1]}, "без сбоев"),
])
def test_verdict_names_the_broken_step(row, part):
    assert part in _show_module().verdict(row)
