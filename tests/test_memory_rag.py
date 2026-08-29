"""tests/test_memory_rag.py — RAG по 🧠 Память: индексация / семантический поиск (#184).

Моки Voyage (_embed) и БД (get_engine — фейковый engine, см. _rag_fake_engine),
как test_rag_triplets.py/test_rag_smoke.py. Реальной сети/БД нет.
"""
from __future__ import annotations

import core.memory_rag as memory_rag
from tests._rag_fake_engine import FakeEngine, boom_engine


# ── _embed_text ──────────────────────────────────────────────────────────────

def test_embed_text_joins_nonempty_parts():
    assert memory_rag._embed_text("факт", "связь", "🏠 Быт") == "факт связь 🏠 Быт"


def test_embed_text_skips_empty_parts():
    assert memory_rag._embed_text("факт", "", "") == "факт"


def test_embed_text_empty_fact_empty_result():
    assert memory_rag._embed_text("", "", "") == ""


# ── index_memory ─────────────────────────────────────────────────────────────

def test_index_memory_empty_text_is_noop(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    ok = memory_rag.index_memory("1", "")
    assert ok is False
    assert eng.calls == []


def test_index_memory_no_key_graceful(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(memory_rag, "_embed", lambda t, input_type="document": [])
    ok = memory_rag.index_memory("1", "факт")
    assert ok is False
    assert eng.calls == []  # _embed вернул [] (нет ключа) — до БД не дошло


def test_index_memory_builds_update(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(
        memory_rag, "_embed",
        lambda t, input_type="document": [[0.1] * 1024],
    )
    ok = memory_rag.index_memory("42", "факт про кота")
    assert ok is True
    assert len(eng.calls) == 1
    sql, params = eng.calls[0]
    assert "UPDATE memories SET embedding" in sql
    assert params["id"] == 42
    assert params["e"].startswith("[0.1,")


def test_index_memory_db_down_graceful():
    ok_calls = []

    def fake_embed(t, input_type="document"):
        ok_calls.append(t)
        return [[0.1] * 1024]

    import unittest.mock as mock
    with mock.patch.object(memory_rag, "_embed", fake_embed), \
         mock.patch.object(memory_rag, "get_engine", boom_engine):
        ok = memory_rag.index_memory("1", "факт")
    assert ok is False
    assert ok_calls == ["факт"]  # embed вызван, а вот БД упала — не раскрылось


# ── index_memories_batch ─────────────────────────────────────────────────────

def test_index_memories_batch_one_embed_call_for_n_rows(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    calls = []

    def fake_embed(texts, input_type="document"):
        calls.append(texts)
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(memory_rag, "_embed", fake_embed)
    items = [
        {"memory_id": "1", "embed_text": "факт раз"},
        {"memory_id": "2", "embed_text": "факт два"},
        {"memory_id": "3", "embed_text": ""},  # пустой — пропускается
    ]
    n = memory_rag.index_memories_batch(items)
    assert n == 2
    assert len(calls) == 1 and calls[0] == ["факт раз", "факт два"]
    assert len(eng.calls) == 2  # два UPDATE в одном batch


def test_index_memories_batch_empty_input():
    assert memory_rag.index_memories_batch([]) == 0
    assert memory_rag.index_memories_batch([{"memory_id": "1", "embed_text": ""}]) == 0


# ── search_memory_semantic ───────────────────────────────────────────────────

def _row(id_=1, fact="факт", category="🏠 Быт", score=0.9):
    return {
        "id": id_, "fact_text": fact, "key_name": "k", "value_text": "",
        "category": category, "scope": "global", "source": "auto",
        "related_to": "", "is_current": True, "is_archived": False,
        "user_notion_id": "", "created_at": None, "updated_at": None,
        "score": score,
    }


def test_search_memory_semantic_builds_scope_filter(monkeypatch):
    eng = FakeEngine(rows=[_row()])
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(memory_rag, "_embed", lambda t, input_type="query": [[0.2] * 1024])

    out = memory_rag.search_memory_semantic("гай", scope="nexus", top_k=5)
    assert len(out) == 1 and out[0].fact == "факт"
    sql, params = eng.calls[0]
    assert "scope = :scope OR scope = 'global'" in sql
    assert params["scope"] == "nexus"
    assert params["k"] == 5


def test_search_memory_semantic_no_scope_no_filter(monkeypatch):
    eng = FakeEngine(rows=[])
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(memory_rag, "_embed", lambda t, input_type="query": [[0.2] * 1024])

    out = memory_rag.search_memory_semantic("гай")
    assert out == []
    sql, params = eng.calls[0]
    assert "scope" not in params


def test_search_memory_semantic_empty_query_no_call(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    out = memory_rag.search_memory_semantic("")
    assert out == []
    assert eng.calls == []


def test_search_memory_semantic_db_down_graceful():
    import unittest.mock as mock
    with mock.patch.object(memory_rag, "_embed", lambda t, input_type="query": [[0.2] * 1024]), \
         mock.patch.object(memory_rag, "get_engine", boom_engine):
        out = memory_rag.search_memory_semantic("гай")
    assert out == []


def test_search_memory_semantic_no_key_graceful(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(memory_rag, "_embed", lambda t, input_type="query": [])
    out = memory_rag.search_memory_semantic("гай")
    assert out == []
    assert eng.calls == []


def test_search_memory_semantic_no_min_score_returns_all(monkeypatch):
    """#185: без min_score (дефолт) векторный поиск НЕ фильтрует — top_k
    соседей возвращаются как есть, даже с низким score."""
    eng = FakeEngine(rows=[_row(1, score=0.9), _row(2, score=0.1)])
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(memory_rag, "_embed", lambda t, input_type="query": [[0.2] * 1024])

    out = memory_rag.search_memory_semantic("гай")
    assert len(out) == 2


def test_search_memory_semantic_min_score_filters_low_scores(monkeypatch):
    eng = FakeEngine(rows=[_row(1, score=0.9), _row(2, score=0.1)])
    monkeypatch.setattr(memory_rag, "get_engine", lambda: eng)
    monkeypatch.setattr(memory_rag, "_embed", lambda t, input_type="query": [[0.2] * 1024])

    out = memory_rag.search_memory_semantic("гай", min_score=0.5)
    assert len(out) == 1 and out[0].id == "1"


# ── rerank_memory_candidates (ADR-0021, #185) ────────────────────────────────

import asyncio
from unittest.mock import AsyncMock, patch

from core.repos.pg_memory_repo import Memory


def _mem(id_="1", fact="факт", category="", related_to=""):
    return Memory(id=id_, fact=fact, category=category, related_to=related_to)


def test_rerank_filters_out_rejected_top_candidate():
    """Топ-1 по cosine distance («луна-хронотип», СДВГ-заметка), но
    семантически не про то — Haiku его отклоняет, остаётся только кот."""
    top1 = _mem("1", fact="совиный хронотип, поздний подъём", category="🦋 СДВГ")
    cat = _mem("2", fact="Луна — кличка кота", category="🐾 Коты")
    with patch.object(memory_rag, "ask_claude", AsyncMock(return_value='["2"]')):
        out = asyncio.run(
            memory_rag.rerank_memory_candidates("что я помню про луну", [top1, cat])
        )
    assert [m.id for m in out] == ["2"]


def test_rerank_invalid_json_returns_empty():
    with patch.object(memory_rag, "ask_claude", AsyncMock(return_value="не JSON, просто текст")):
        out = asyncio.run(memory_rag.rerank_memory_candidates("запрос", [_mem("1")]))
    assert out == []


def test_rerank_json_not_a_list_returns_empty():
    with patch.object(memory_rag, "ask_claude", AsyncMock(return_value='{"ids": ["1"]}')):
        out = asyncio.run(memory_rag.rerank_memory_candidates("запрос", [_mem("1")]))
    assert out == []


def test_rerank_empty_candidates_no_call():
    ask = AsyncMock()
    with patch.object(memory_rag, "ask_claude", ask):
        out = asyncio.run(memory_rag.rerank_memory_candidates("запрос", []))
    assert out == []
    ask.assert_not_called()


def test_rerank_markdown_fenced_json_parsed():
    with patch.object(memory_rag, "ask_claude", AsyncMock(return_value='```json\n["1"]\n```')):
        out = asyncio.run(memory_rag.rerank_memory_candidates("запрос", [_mem("1"), _mem("2")]))
    assert [m.id for m in out] == ["1"]
