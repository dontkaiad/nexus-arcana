"""core/rag.py — RAG-инфраструктура Arcana (Voyage AI эмбеддинги + Qdrant).

RAG-0: только фундамент — ленивые клиенты, `_embed`, `ensure_collection`.
Индексацию/поиск триплетов НЕ подключаем здесь — это RAG-AB (следующий коммит).

Принципы:
- Всё graceful: нет VOYAGE_API_KEY / Qdrant недоступен / SDK не установлен →
  warning в лог + пусто/no-op, БЕЗ исключения. Боты не падают.
- Импорты voyageai / qdrant_client — ЛЕНИВЫЕ (внутри функций), чтобы
  `import core.rag` работал даже без установленных пакетов (тесты, dev).

Отличия от klgpff-bot (намеренно, не копипаст):
- Модель voyage-4-lite, dim 1024 (дефолт модели) — ДРУГАЯ модель, чем у klgpff
  (voyage-3-lite, 512) → отдельный бесплатный пул токенов Voyage, не конфликтует.
- Qdrant — ОБЩИЙ сервис klgpff, опубликованный на хост 127.0.0.1:6333.
  Подключаемся через хост (QDRANT_HOST/QDRANT_PORT), klgpff не трогаем.

Gotcha для RAG-AB (поиск): у qdrant-client метод НЕ `.search()`, а
`.query_points(...)`, результаты — через `.points`.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Union

logger = logging.getLogger("core.rag")

# Voyage: отдельная модель/пул от klgpff (voyage-3-lite/512).
VOYAGE_MODEL = "voyage-4-lite"
VOYAGE_DIM = 1024

_voyage_client = None
_qdrant_client = None


def get_voyage_client():
    """Ленивый Voyage-клиент. None если ключа нет или SDK не установлен."""
    global _voyage_client
    if _voyage_client is not None:
        return _voyage_client
    key = os.getenv("VOYAGE_API_KEY") or ""
    if not key:
        logger.warning("VOYAGE_API_KEY пуст — RAG-эмбеддинги отключены (no-op)")
        return None
    try:
        import voyageai
        _voyage_client = voyageai.Client(api_key=key)
        return _voyage_client
    except Exception as e:  # SDK не установлен / иная ошибка инициализации
        logger.warning("Voyage init failed: %s", e)
        return None


def get_qdrant_client():
    """Ленивый Qdrant-клиент через ХОСТ. None если SDK нет/коннект не поднялся.

    Host из QDRANT_HOST (дефолт host.docker.internal — резолвится в compose
    через extra_hosts host-gateway), порт из QDRANT_PORT (дефолт 6333)."""
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client
    host = os.getenv("QDRANT_HOST") or "host.docker.internal"
    try:
        port = int(os.getenv("QDRANT_PORT") or 6333)
    except (TypeError, ValueError):
        port = 6333
    try:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(host=host, port=port, timeout=5)
        return _qdrant_client
    except Exception as e:
        logger.warning("Qdrant init failed (%s:%s): %s", host, port, e)
        return None


def _embed(
    texts: Union[str, List[str]], input_type: str = "document"
) -> List[List[float]]:
    """Voyage-эмбеддинги. Возвращает список векторов (пусто при ошибке/без ключа).

    input_type:
      - "document" — для индексации (хранимые тексты),
      - "query"    — для поисковых запросов.
    Voyage оптимизирует эмбеддинг под input_type, поэтому прокидываем явно."""
    if isinstance(texts, str):
        texts = [texts]
    texts = [t for t in (texts or []) if t]
    if not texts:
        return []
    client = get_voyage_client()
    if client is None:
        return []
    try:
        resp = client.embed(texts, model=VOYAGE_MODEL, input_type=input_type)
        return list(resp.embeddings or [])
    except Exception as e:
        logger.warning("Voyage embed failed (%s texts): %s", len(texts), e)
        return []


def ensure_collection(
    name: str, dim: int = VOYAGE_DIM, distance: Optional[object] = None
) -> bool:
    """Создаёт коллекцию Qdrant, если её ещё нет. Идемпотентно.

    Возвращает True если коллекция существует/создана, False — если Qdrant
    недоступен или произошла ошибка (graceful, без исключения).
    distance по умолчанию — Cosine."""
    if not name:
        return False
    client = get_qdrant_client()
    if client is None:
        return False
    try:
        from qdrant_client.models import Distance, VectorParams
        dist = distance if distance is not None else Distance.COSINE
        # get_collections — стабильный API во всех версиях qdrant-client.
        existing = {c.name for c in client.get_collections().collections}
        if name in existing:
            return True
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=dist),
        )
        logger.info("Qdrant: создана коллекция %s (dim=%s)", name, dim)
        return True
    except Exception as e:
        logger.warning("ensure_collection(%s) failed: %s", name, e)
        return False
