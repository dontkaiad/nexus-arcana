"""tests/test_rag_triplets.py — RAG-AB: индексация / поиск / инъекция голоса (#166).

Моки Voyage (_embed) и БД (get_engine — фейковый engine, см. _rag_fake_engine).
Реальной сети/БД НЕТ; реальное исполнение pgvector-SQL покрыто смоуком/backfill.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import core.rag as rag
from tests._rag_fake_engine import FakeEngine, boom_engine


# ── index_triplet ─────────────────────────────────────────────────────────────

def test_index_triplet_text_from_nonempty_parts(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
    captured = {}

    def fake_embed(text, input_type="document"):
        captured["text"] = text
        captured["input_type"] = input_type
        return [[0.1] * rag.VOYAGE_DIM]

    monkeypatch.setattr(rag, "_embed", fake_embed)

    ok = rag.index_triplet(
        "42", "8 мечей, шут", "что чувствует", "",  # interpretation ПУСТОЙ
        client_id="5", session_name="Вадим", occurred_at="2026-06-22",
    )
    assert ok is True
    # пустой interpretation НЕ попал в текст эмбеддинга
    assert captured["text"] == "8 мечей, шут что чувствует"
    assert captured["input_type"] == "document"

    # один INSERT; параметры строки
    assert len(eng.calls) == 1
    _sql, row = eng.calls[0]
    assert row["session_id"] == 42            # числовой ключ из "42"
    assert row["client_id"] == 5              # bigint id клиента
    assert row["interp_excerpt"] == ""        # пустая трактовка → пусто
    assert row["cards"] == "8 мечей, шут"


def test_index_triplet_interp_excerpt_strips_html(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="document": [[0.1] * rag.VOYAGE_DIM])
    rag.index_triplet("1", "карты", "вопрос", "<h3>Итог</h3><p>тупик</p>")
    row = eng.calls[0][1]
    assert row["interp_excerpt"] == "Итог тупик"
    assert row["interpretation"] == "<h3>Итог</h3><p>тупик</p>"  # полная — тоже хранится


# ── search_triplets ───────────────────────────────────────────────────────────

def test_search_with_client_id_builds_filter(monkeypatch):
    eng = FakeEngine(rows=[{
        "session_id": 1, "cards": "X", "interp_excerpt": "e", "score": 0.9,
        "client_id": 5, "session_name": None, "occurred_at": None,
        "question": None, "bottom_card": None, "deck": None,
    }])
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="query": [[0.2] * rag.VOYAGE_DIM])

    out = rag.search_triplets("запрос", top_k=3, client_id="5")
    assert out and out[0]["score"] == 0.9 and out[0]["triplet_id"] == "1"
    sql, params = eng.calls[0]
    assert "WHERE client_id = :client_id" in sql   # фильтр построен
    assert params["client_id"] == 5
    assert params["k"] == 3


def test_search_without_client_id_no_filter(monkeypatch):
    eng = FakeEngine(rows=[])
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="query": [[0.2] * rag.VOYAGE_DIM])

    assert rag.search_triplets("запрос", top_k=3, client_id=None) == []
    sql, params = eng.calls[0]
    assert "WHERE client_id" not in sql            # фильтра нет
    assert "client_id" not in params


# ── graceful (БД/Voyage недоступны) ───────────────────────────────────────────

def test_graceful_db_down(monkeypatch):
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="document": [[0.1] * rag.VOYAGE_DIM])
    monkeypatch.setattr(rag, "get_engine", boom_engine)
    assert rag.search_triplets("q") == []          # пусто, без исключения
    assert rag.index_triplet("1", "c", "q", "i") is False  # no-op
    assert rag.delete_triplet("1") is False


def test_search_graceful_when_embed_empty(monkeypatch):
    # _embed пуст → search [] ДО касания БД
    monkeypatch.setattr(rag, "get_engine", boom_engine)
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
    client.embed.return_value = type("R", (), {"embeddings": [[1.0], [2.0], [3.0]]})()
    monkeypatch.setattr(rag, "get_voyage_client", lambda: client)
    out = rag._embed(["a", "b", "c"], input_type="document")
    assert out == [[1.0], [2.0], [3.0]]  # порядок входа сохранён
    # один запрос на весь батч (список из 3 текстов)
    client.embed.assert_called_once()
    assert client.embed.call_args.args[0] == ["a", "b", "c"]


def test_index_triplets_batch_single_embed(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
    embed_mock = MagicMock(return_value=[[0.1], [0.2], [0.3]])
    monkeypatch.setattr(rag, "_embed", embed_mock)

    items = [
        {"triplet_id": str(i), "cards": "c", "question": "q", "interpretation": "i",
         "client_id": "5", "session_name": "S", "occurred_at": "2026-06-22"}
        for i in (1, 2, 3)
    ]
    n = rag.index_triplets_batch(items)
    assert n == 3
    embed_mock.assert_called_once()                 # ОДИН _embed на N триплетов
    assert len(eng.calls) == 1                       # один INSERT пакетом
    _sql, rows = eng.calls[0]
    assert [r["session_id"] for r in rows] == [1, 2, 3]


def test_index_triplets_batch_skips_empty_text(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
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
