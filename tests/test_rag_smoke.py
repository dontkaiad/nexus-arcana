"""tests/test_rag_smoke.py — RAG-0 smoke (core/rag.py).

Проверяем фундамент БЕЗ реальных сетевых вызовов к Qdrant/Voyage:
- core.rag импортируется без установленных voyageai/qdrant-client (ленивые импорты);
- ensure_collection при недоступном Qdrant graceful-degrade-ит (False, без исключения);
- _embed без VOYAGE_API_KEY возвращает [] (без исключения);
- get_qdrant_client при ошибке коннекта → None (без исключения, без сети).
"""
from __future__ import annotations

import sys
import types

import core.rag as rag


def test_import_and_constants():
    assert hasattr(rag, "ensure_collection")
    assert hasattr(rag, "_embed")
    assert hasattr(rag, "get_qdrant_client")
    assert hasattr(rag, "get_voyage_client")
    # Отличие от klgpff (voyage-3-lite/512) зафиксировано: voyage-4-lite/1024.
    assert rag.VOYAGE_MODEL == "voyage-4-lite"
    assert rag.VOYAGE_DIM == 1024


def test_ensure_collection_graceful_when_qdrant_unavailable(monkeypatch):
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: None)
    # Не кидает, возвращает False — никакого сетевого вызова.
    assert rag.ensure_collection("arcana_triplets") is False


def test_ensure_collection_empty_name_noop(monkeypatch):
    monkeypatch.setattr(rag, "get_qdrant_client", lambda: None)
    assert rag.ensure_collection("") is False


def test_embed_graceful_without_key(monkeypatch):
    monkeypatch.setattr(rag, "get_voyage_client", lambda: None)
    assert rag._embed(["какой-то текст"], input_type="document") == []
    assert rag._embed("строка-запрос", input_type="query") == []
    assert rag._embed([]) == []  # пустой ввод — тоже []


def test_voyage_client_none_without_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    rag._voyage_client = None  # сброс кеша
    assert rag.get_voyage_client() is None


def test_qdrant_client_graceful_on_connect_error(monkeypatch):
    """Заглушка qdrant_client, бросающая при инициализации → None, без сети."""
    rag._qdrant_client = None  # сброс кеша
    fake = types.ModuleType("qdrant_client")

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("dead host (mock)")

    fake.QdrantClient = _Boom
    monkeypatch.setitem(sys.modules, "qdrant_client", fake)
    assert rag.get_qdrant_client() is None
    rag._qdrant_client = None  # не оставляем мусор для других тестов
