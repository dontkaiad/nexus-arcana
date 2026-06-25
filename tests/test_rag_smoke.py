"""tests/test_rag_smoke.py — smoke core/rag.py (Voyage AI + pgvector).

Без реальных сетевых/БД вызовов:
- core.rag импортируется без voyageai (ленивый импорт);
- абстракция на месте (index/search/delete/batch + ensure_collection);
- ensure_collection: True если таблица есть, False если нет/БД недоступна (graceful);
- index+search smoke на фейковом engine (построение SQL + контракт);
- _embed без VOYAGE_API_KEY → [] (graceful);
- БД недоступна → index False / search [] / delete False, без исключения.
"""
from __future__ import annotations

import core.rag as rag
from tests._rag_fake_engine import FakeEngine, boom_engine


def test_import_and_constants():
    # абстракция (имена, на которые завязаны хендлеры/backfill)
    for name in ("ensure_collection", "index_triplet", "index_triplets_batch",
                 "search_triplets", "delete_triplet", "_embed", "get_voyage_client"):
        assert hasattr(rag, name), name
    # Отличие от klgpff (voyage-3-lite/512) зафиксировано: voyage-4-lite/1024.
    assert rag.VOYAGE_MODEL == "voyage-4-lite"
    assert rag.VOYAGE_DIM == 1024


def test_ensure_collection_true_when_table_exists(monkeypatch):
    monkeypatch.setattr(rag, "get_engine", lambda: FakeEngine(scalar=True))
    assert rag.ensure_collection("arcana_triplets") is True


def test_ensure_collection_false_when_table_missing(monkeypatch):
    monkeypatch.setattr(rag, "get_engine", lambda: FakeEngine(scalar=False))
    assert rag.ensure_collection("arcana_triplets") is False


def test_ensure_collection_empty_name_noop(monkeypatch):
    # пустое имя → False ДО касания БД
    monkeypatch.setattr(rag, "get_engine", boom_engine)
    assert rag.ensure_collection("") is False


def test_ensure_collection_graceful_when_db_down(monkeypatch):
    monkeypatch.setattr(rag, "get_engine", boom_engine)
    assert rag.ensure_collection("arcana_triplets") is False  # без исключения


def test_index_and_search_smoke(monkeypatch):
    """smoke = index + search через абстракцию (фейковый engine)."""
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="document": [[0.1] * rag.VOYAGE_DIM])
    eng = FakeEngine()
    monkeypatch.setattr(rag, "get_engine", lambda: eng)
    assert rag.index_triplet("7", "Королева Мечей", "что чувствует", "<p>тон</p>") is True
    assert len(eng.calls) == 1  # один INSERT

    # search: фейковая строка результата → контракт хендлера
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="query": [[0.2] * rag.VOYAGE_DIM])
    eng2 = FakeEngine(rows=[{
        "session_id": 7, "client_id": None, "session_name": None,
        "occurred_at": None, "question": "q", "cards": "Королева Мечей",
        "bottom_card": None, "deck": None, "interp_excerpt": "тон", "score": 0.88,
    }])
    monkeypatch.setattr(rag, "get_engine", lambda: eng2)
    out = rag.search_triplets("Королева Мечей")
    assert out and out[0]["triplet_id"] == "7"
    assert out[0]["cards"] == "Королева Мечей" and out[0]["interp_excerpt"] == "тон"
    assert out[0]["score"] == 0.88


def test_embed_graceful_without_key(monkeypatch):
    monkeypatch.setattr(rag, "get_voyage_client", lambda: None)
    assert rag._embed(["какой-то текст"], input_type="document") == []
    assert rag._embed("строка-запрос", input_type="query") == []
    assert rag._embed([]) == []  # пустой ввод — тоже []


def test_voyage_client_none_without_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    rag._voyage_client = None  # сброс кеша
    assert rag.get_voyage_client() is None


def test_store_graceful_when_db_down(monkeypatch):
    """БД недоступна (get_engine бросает) → index/search/delete не падают."""
    monkeypatch.setattr(rag, "_embed", lambda t, input_type="document": [[0.1] * rag.VOYAGE_DIM])
    monkeypatch.setattr(rag, "get_engine", boom_engine)
    assert rag.index_triplet("1", "c", "q", "i") is False
    assert rag.search_triplets("q") == []
    assert rag.delete_triplet("1") is False
