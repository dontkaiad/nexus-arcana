"""tests/test_rag_triplets.py — RAG-AB: индексация / поиск / инъекция голоса (#166).

Моки Voyage (_embed) и Qdrant (клиент + qdrant_client.models). Реальной сети НЕТ.
"""
from __future__ import annotations

import sys
import types

import pytest
from unittest.mock import AsyncMock, MagicMock

import core.rag as rag


# ── фейковый qdrant_client.models для index/search/delete путей ───────────────
class _PointStruct:
    def __init__(self, id=None, vector=None, payload=None):
        self.id = id
        self.vector = vector
        self.payload = payload


class _Filter:
    def __init__(self, must=None):
        self.must = must


class _FieldCondition:
    def __init__(self, key=None, match=None):
        self.key = key
        self.match = match


class _MatchValue:
    def __init__(self, value=None):
        self.value = value


class _PointIdsList:
    def __init__(self, points=None):
        self.points = points


@pytest.fixture
def fake_models(monkeypatch):
    mod = types.ModuleType("qdrant_client.models")
    mod.PointStruct = _PointStruct
    mod.Filter = _Filter
    mod.FieldCondition = _FieldCondition
    mod.MatchValue = _MatchValue
    mod.PointIdsList = _PointIdsList
    parent = types.ModuleType("qdrant_client")
    parent.models = mod
    monkeypatch.setitem(sys.modules, "qdrant_client", parent)
    monkeypatch.setitem(sys.modules, "qdrant_client.models", mod)
    return mod


def _scored(payload, score=0.9):
    return types.SimpleNamespace(payload=payload, score=score)


# ── index_triplet ─────────────────────────────────────────────────────────────

def test_index_triplet_text_from_nonempty_parts(monkeypatch, fake_models):
    client = MagicMock()
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: client)
    captured = {}

    def fake_embed(text, input_type="document"):
        captured["text"] = text
        captured["input_type"] = input_type
        return [[0.1] * rag.VOYAGE_DIM]

    monkeypatch.setattr(rag, "_embed", fake_embed)

    ok = rag.index_triplet(
        "42", "8 мечей, шут", "что чувствует", "",  # interpretation ПУСТОЙ
        client_id="c1", session_name="Вадим", occurred_at="2026-06-22",
    )
    assert ok is True
    # пустой interpretation НЕ попал в текст эмбеддинга
    assert captured["text"] == "8 мечей, шут что чувствует"
    assert captured["input_type"] == "document"

    client.upsert.assert_called_once()
    pts = client.upsert.call_args.kwargs["points"]
    assert pts[0].id == 42  # числовой point id из "42"
    assert pts[0].payload["triplet_id"] == "42"
    assert pts[0].payload["client_id"] == "c1"
    assert pts[0].payload["interp_excerpt"] == ""  # пустая трактовка → пусто


def test_index_triplet_interp_excerpt_strips_html(monkeypatch, fake_models):
    client = MagicMock()
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="document": [[0.1]])
    rag.index_triplet("1", "карты", "вопрос", "<h3>Итог</h3><p>тупик</p>")
    payload = client.upsert.call_args.kwargs["points"][0].payload
    assert payload["interp_excerpt"] == "Итог тупик"


# ── search_triplets ───────────────────────────────────────────────────────────

def test_search_with_client_id_builds_filter(monkeypatch, fake_models):
    client = MagicMock()
    client.query_points.return_value = types.SimpleNamespace(
        points=[_scored({"triplet_id": "1", "cards": "X", "interp_excerpt": "e"})]
    )
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="query": [[0.2]])

    out = rag.search_triplets("запрос", top_k=3, client_id="c1")
    assert out and out[0]["score"] == 0.9 and out[0]["triplet_id"] == "1"
    qf = client.query_points.call_args.kwargs["query_filter"]
    assert qf is not None
    assert qf.must[0].key == "client_id"
    assert qf.must[0].match.value == "c1"


def test_search_without_client_id_no_filter(monkeypatch, fake_models):
    client = MagicMock()
    client.query_points.return_value = types.SimpleNamespace(points=[])
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="query": [[0.2]])

    assert rag.search_triplets("запрос", top_k=3, client_id=None) == []
    assert client.query_points.call_args.kwargs["query_filter"] is None


# ── graceful (Qdrant/Voyage недоступны) ───────────────────────────────────────

def test_graceful_qdrant_down(monkeypatch):
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: None)
    assert rag.search_triplets("q") == []          # пусто, без исключения
    assert rag.index_triplet("1", "c", "q", "i") is False  # no-op
    assert rag.delete_triplet("1") is False


def test_search_graceful_when_embed_empty(monkeypatch):
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: MagicMock())
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="query": [])
    assert rag.search_triplets("q") == []


# ── интеграция A: _rag_voice_block (consistency-of-voice) ─────────────────────

@pytest.mark.asyncio
async def test_voice_block_present_when_similar(monkeypatch):
    from arcana.handlers import sessions as S
    monkeypatch.setattr(
        "core.rag.search_triplets",
        lambda q, k, c: [{"triplet_id": "7", "cards": "8 мечей",
                          "interp_excerpt": "ловушка в чувствах"}],
    )
    block = await S._rag_voice_block("8 мечей, шут", "что чувствует")
    assert S._RAG_VOICE_HEADER in block
    assert "ловушка в чувствах" in block
    assert "8 мечей" in block


@pytest.mark.asyncio
async def test_voice_block_empty_when_no_similar(monkeypatch):
    from arcana.handlers import sessions as S
    monkeypatch.setattr("core.rag.search_triplets", lambda q, k, c: [])
    assert await S._rag_voice_block("X", "Y") == ""


@pytest.mark.asyncio
async def test_voice_block_excludes_current_triplet(monkeypatch):
    from arcana.handlers import sessions as S
    monkeypatch.setattr(
        "core.rag.search_triplets",
        lambda q, k, c: [
            {"triplet_id": "7", "cards": "a", "interp_excerpt": "его"},
            {"triplet_id": "9", "cards": "b", "interp_excerpt": "её"},
        ],
    )
    block = await S._rag_voice_block("X", "Y", exclude_id="7")
    assert "его" not in block
    assert "её" in block


@pytest.mark.asyncio
async def test_voice_block_skips_empty_interp(monkeypatch):
    from arcana.handlers import sessions as S
    monkeypatch.setattr(
        "core.rag.search_triplets",
        lambda q, k, c: [{"triplet_id": "7", "cards": "a", "interp_excerpt": ""}],
    )
    assert await S._rag_voice_block("X", "Y") == ""


# ── batch embedding (порядок + один запрос) ───────────────────────────────────

def test_embed_batch_returns_vectors_in_order(monkeypatch):
    client = MagicMock()
    client.embed.return_value = types.SimpleNamespace(embeddings=[[1.0], [2.0], [3.0]])
    monkeypatch.setattr(rag, "get_voyage_client", lambda: client)
    out = rag._embed(["a", "b", "c"], input_type="document")
    assert out == [[1.0], [2.0], [3.0]]  # порядок входа сохранён
    # один запрос на весь батч (список из 3 текстов)
    client.embed.assert_called_once()
    assert client.embed.call_args.args[0] == ["a", "b", "c"]


def test_index_triplets_batch_single_embed(monkeypatch, fake_models):
    client = MagicMock()
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: client)
    embed_mock = MagicMock(return_value=[[0.1], [0.2], [0.3]])
    monkeypatch.setattr(rag, "_embed", embed_mock)

    items = [
        {"triplet_id": str(i), "cards": "c", "question": "q", "interpretation": "i",
         "client_id": "c1", "session_name": "S", "occurred_at": "2026-06-22"}
        for i in (1, 2, 3)
    ]
    n = rag.index_triplets_batch(items)
    assert n == 3
    embed_mock.assert_called_once()                 # ОДИН _embed на N триплетов
    assert client.upsert.call_count == 1            # один upsert пакетом
    pts = client.upsert.call_args.kwargs["points"]
    assert [p.id for p in pts] == [1, 2, 3]


def test_index_triplets_batch_skips_empty_text(monkeypatch, fake_models):
    client = MagicMock()
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: client)
    captured = {}
    monkeypatch.setattr(rag, "_embed", lambda texts, input_type="document": (
        captured.update(texts=texts) or [[0.1]] * len(texts)))
    items = [
        {"triplet_id": "1", "cards": "", "question": "", "interpretation": ""},  # пустой
        {"triplet_id": "2", "cards": "c", "question": "q", "interpretation": "i"},
    ]
    n = rag.index_triplets_batch(items)
    assert n == 1                                   # пустой пропущен
    assert len(captured["texts"]) == 1


# ── rate-limit ловится отдельно ───────────────────────────────────────────────

def test_rate_limit_caught_separately(monkeypatch, caplog):
    client = MagicMock()

    class _RL(Exception):
        status_code = 429

    client.embed.side_effect = _RL("rate limited")
    monkeypatch.setattr(rag, "get_voyage_client", lambda: client)
    import logging
    with caplog.at_level(logging.WARNING, logger="core.rag"):
        out = rag._embed(["a"])
    assert out == []
    assert any("rate limit" in r.message.lower() for r in caplog.records)


def test_other_error_not_treated_as_rate_limit(monkeypatch, caplog):
    client = MagicMock()
    client.embed.side_effect = RuntimeError("boom")  # без status_code
    monkeypatch.setattr(rag, "get_voyage_client", lambda: client)
    import logging
    with caplog.at_level(logging.WARNING, logger="core.rag"):
        out = rag._embed(["a"])
    assert out == []
    assert not any("rate limit" in r.message.lower() for r in caplog.records)
    assert rag._is_rate_limit(RuntimeError("boom")) is False


# ── backfill (батч) ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_uses_batch(monkeypatch):
    import scripts.backfill_arcana_rag as bf
    from arcana.repos.sessions_repo import TripletEntry

    rows = [
        TripletEntry(
            id=str(i), question="q", cards="c", interpretation="x",
            deck="Уэйт", session_name="S", client_id="c1", date="2026-06-22",
        )
        for i in (1, 2, 3)
    ]
    monkeypatch.setattr(bf.PgSessionsRepo, "list_all", AsyncMock(return_value=rows))
    seen = {}
    monkeypatch.setattr(
        bf, "index_triplets_batch",
        lambda items: seen.update(ids=[t["triplet_id"] for t in items]) or len(items),
    )
    n = await bf.backfill()
    assert n == 3
    assert seen["ids"] == ["1", "2", "3"]  # один батч на всю историю
