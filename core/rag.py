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

# Коллекция Qdrant с триплетами Арканы (создаётся ensure_collection).
COLLECTION_TRIPLETS = "arcana_triplets"

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


# ────────────────────────── Триплеты (RAG-AB) ──────────────────────────────

def _point_id(triplet_id) -> object:
    """PG id (int-строка) → числовой point id Qdrant (детерминированно,
    upsert перезапишет при правке). Нечисловой id → стабильный uuid5."""
    s = str(triplet_id)
    if s.isdigit():
        return int(s)
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"arcana_triplet:{s}"))


def _interp_excerpt(interpretation: str, limit: int = 400) -> str:
    """Короткий plain-text огрызок трактовки для инъекции «стиль/тон» —
    снимаем HTML-теги, схлопываем пробелы, режем по limit."""
    if not interpretation:
        return ""
    import re
    txt = re.sub(r"<[^>]+>", " ", interpretation)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def index_triplet(
    triplet_id,
    cards: str,
    question: str,
    interpretation: str,
    client_id: Optional[str] = None,
    session_name: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> bool:
    """Индексирует триплет в arcana_triplets (upsert по детерминированному id).

    Текст эмбеддинга = непустые части {cards, question, interpretation}
    (пустой interpretation не ломает — собираем из того, что есть).
    Graceful: Qdrant/Voyage недоступны → warning + False, без исключения."""
    client = get_qdrant_client()
    if client is None:
        return False
    parts = [str(p).strip() for p in (cards, question, interpretation) if p and str(p).strip()]
    text = " ".join(parts)
    if not text:
        logger.warning("index_triplet(%s): пустой текст — пропуск", triplet_id)
        return False
    vecs = _embed(text, input_type="document")
    if not vecs:
        return False  # _embed уже залогировал (нет ключа/ошибка)
    try:
        from qdrant_client.models import PointStruct
        client.upsert(
            collection_name=COLLECTION_TRIPLETS,
            points=[PointStruct(
                id=_point_id(triplet_id),
                vector=vecs[0],
                payload={
                    "triplet_id": str(triplet_id),
                    "client_id": str(client_id) if client_id else None,
                    "session_name": session_name or None,
                    "occurred_at": occurred_at or None,
                    "question": question or None,
                    "cards": cards or None,
                    # огрызок трактовки — чтобы поиск отдавал текст для инъекции
                    # «стиль/тон» без обратного похода в PG.
                    "interp_excerpt": _interp_excerpt(interpretation),
                },
            )],
        )
        return True
    except Exception as e:
        logger.warning("index_triplet(%s) upsert failed: %s", triplet_id, e)
        return False


def search_triplets(
    query_text: str, top_k: int = 5, client_id: Optional[str] = None
) -> List[dict]:
    """Семантический поиск похожих триплетов. Возвращает список payload+score.

    client_id задан → Filter по payload.client_id (история одного клиента);
    None → по ВСЕМ (для консистентности голоса автора).
    Graceful: Qdrant/Voyage недоступны → []. Без исключения."""
    if not query_text or not str(query_text).strip():
        return []
    client = get_qdrant_client()
    if client is None:
        return []
    vecs = _embed(query_text, input_type="query")
    if not vecs:
        return []
    try:
        qfilter = None
        if client_id:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            qfilter = Filter(must=[
                FieldCondition(key="client_id", match=MatchValue(value=str(client_id)))
            ])
        # gotcha: метод query_points (НЕ search), результаты — через .points.
        resp = client.query_points(
            collection_name=COLLECTION_TRIPLETS,
            query=vecs[0],
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        out: List[dict] = []
        for p in resp.points:
            item = dict(p.payload or {})
            item["score"] = p.score
            out.append(item)
        return out
    except Exception as e:
        logger.warning("search_triplets failed: %s", e)
        return []


def delete_triplet(triplet_id) -> bool:
    """Удаляет точку триплета из arcana_triplets (для правки/удаления).
    Вызов из хендлеров правки в этом коммите НЕ подключён — только функция.
    Graceful: Qdrant недоступен → False, без исключения."""
    client = get_qdrant_client()
    if client is None:
        return False
    try:
        from qdrant_client.models import PointIdsList
        client.delete(
            collection_name=COLLECTION_TRIPLETS,
            points_selector=PointIdsList(points=[_point_id(triplet_id)]),
        )
        return True
    except Exception as e:
        logger.warning("delete_triplet(%s) failed: %s", triplet_id, e)
        return False
