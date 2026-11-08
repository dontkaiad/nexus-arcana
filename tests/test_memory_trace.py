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


def test_purge_old_removes_only_old_rows():
    from core.memory_trace import purge_old
    with get_engine().begin() as c:
        for ago in (45, 31, 29, 1):
            c.execute(text("INSERT INTO memory_trace (bot, user_id, question, ts) VALUES ('nexus', :u, :q, now() - make_interval(days => :d))"),
                      {"u": MARK, "q": f"q{ago}", "d": ago})
    purge_old()
    with get_engine().connect() as c:
        left = sorted(r[0] for r in c.execute(text("SELECT question FROM memory_trace WHERE user_id = :u"), {"u": MARK}))
    assert left == ["q1", "q29"]


def test_purge_job_is_scheduled_daily():
    src = (REPO / "nexus" / "nexus_bot.py").read_text(encoding="utf-8")
    assert 'id="memory_trace_purge"' in src and "purge_old_traces" in src


def test_trace_row_is_written_on_the_real_bot_path_handle_text():
    """Регрессия: Memory.id — строки, а rrf_ids/final_ids — bigint[]; запись падала молча, трасс не было вообще.
    Путь: handle_text → classify → process_item → search_memory → answer (реальный гибрид) → generate (API лежит → fallback)."""
    import asyncio
    import re
    from types import SimpleNamespace
    from core import memory_answer, memory_search, classifier
    from core.repos.memories_table import memories
    from core.repos.people_tables import people
    from sqlalchemy import delete, insert
    import nexus.nexus_bot as nb

    from tests._people_helpers import insert_person
    me = insert_person(MARK, "Я", is_self=True, status="confirmed")
    with get_engine().begin() as c:
        for i, t in enumerate(("не ем сладкое", "не ем грибы", "не ем сыр")):
            c.execute(insert(memories).values(fact_text=t, key_name="person_%d_dislikes_%08x" % (me, i), category="🛒 Предпочтения",
                                               user_id=MARK, person_id=me, review_status="confirmed"))
    msg = SimpleNamespace(
        text="что я не ем", from_user=SimpleNamespace(id=700000002, is_bot=False), chat=SimpleNamespace(id=700000002),
        reply_to_message=None, voice=None, photo=None, bot=SimpleNamespace(send_chat_action=AsyncMock()),
        answer=AsyncMock(), reply=AsyncMock())
    no = AsyncMock(return_value=False)

    async def rerank_all(prompt="", **kw):
        return json.dumps([int(x) for x in re.findall(r"id=(\d+)", prompt)])

    cls = AsyncMock(return_value=json.dumps([{"type": "memory_search", "query": "не ем"}]))
    try:
        with patch.dict("os.environ", {"MEMORY_GENERATE_ANSWER": "1"}), \
                patch.object(classifier, "ask_claude", cls), \
                patch.object(memory_search, "_embed", return_value=[]), \
                patch.object(memory_search, "ask_claude", AsyncMock(side_effect=rerank_all)), \
                patch.object(memory_answer, "ask_claude", AsyncMock(side_effect=RuntimeError("api down"))), \
                patch("core.memory._search_finance", AsyncMock(return_value=[])), \
                patch("nexus.handlers.finance.maybe_payday_reminder", AsyncMock()), \
                patch("nexus.handlers.finance.handle_budget_setup_text", no), \
                patch("nexus.handlers.finance.handle_finance_pending", no), \
                patch("nexus.handlers.lists.handle_list_pending", no), \
                patch.object(nb, "_handle_receipt_clarify", no), \
                patch("nexus.handlers.tasks.maybe_handle_reschedule_pending", no), \
                patch("nexus.handlers.tasks._pending_has", lambda *_: False), \
                patch("nexus.handlers.tasks._get_effective_tz", AsyncMock(return_value=(3, "MSK"))), \
                patch("nexus.handlers.utils.react", AsyncMock()):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(nb.handle_text(msg, user_id=MARK))
            finally:
                loop.close()
                asyncio.set_event_loop(asyncio.new_event_loop())
        with get_engine().connect() as c:
            rows = c.execute(text("SELECT question, route, final_ids, rrf_ids, fts, gen, error FROM memory_trace "
                                  "WHERE user_id = :u"), {"u": MARK}).all()
        assert len(rows) == 1, "трасса не записалась на пути handle_text"
        r = rows[0]
        assert r.question == "что я не ем" and r.error is None and r.route == "hybrid_self"
        assert len(r.final_ids) == 3 and all(isinstance(i, int) for i in r.final_ids)
        assert sorted(r.rrf_ids) == sorted(r.final_ids) and r.fts and r.fts[0][1] == 1
        assert r.gen and r.gen["attempted"] and not r.gen["ok"]            # генерация упала → fallback на список, причина в трассе
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == MARK))
            c.execute(delete(people).where(people.c.user_id == MARK))
