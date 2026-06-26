"""core/rag.py — RAG-инфраструктура Arcana (Voyage AI эмбеддинги + pgvector).

Хранилище векторов — таблица `arcana_triplets` в ТОМ ЖЕ Postgres, что и весь бот
(pgvector), через общий `core.db.get_engine()`. Раньше был Qdrant соседнего
проекта klgpff — отвязались (см. RAG-миграцию Qdrant→pgvector, ADR-0006).

Принципы (без изменений):
- Всё graceful: нет VOYAGE_API_KEY / БД недоступна / pgvector не стоит → warning
  в лог + пусто/no-op, БЕЗ исключения. Боты не падают, расклад работает без RAG.
- Импорт voyageai — ЛЕНИВЫЙ (внутри функций), чтобы `import core.rag` работал
  даже без установленного SDK (тесты, dev).
- Функции синхронные (SQLAlchemy Core / psycopg2), вызываются из хендлеров через
  asyncio.to_thread — как и раньше.

Отличия Voyage от klgpff-bot (намеренно, не копипаст): модель voyage-4-lite,
dim 1024 — ДРУГАЯ, чем у klgpff (voyage-3-lite, 512) → отдельный бесплатный пул
токенов Voyage, не конфликтует.

Передача вектора в SQL: pgvector-литерал `[v1,v2,...]` текстовым параметром +
`CAST(:p AS vector)` (CAST, не `::vector` — иначе SQLAlchemy спутает `:` с
bind-параметром). Поиск — косинус: `embedding <=> CAST(:q AS vector)` (расстояние,
индекс hnsw vector_cosine_ops), score = `1 - расстояние` (как косинус-score Qdrant).
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Union

import sqlalchemy as sa

from core.db import get_engine

logger = logging.getLogger("core.rag")

# Voyage: отдельная модель/пул от klgpff (voyage-3-lite/512).
VOYAGE_MODEL = "voyage-4-lite"
VOYAGE_DIM = 1024

# Таблица pgvector с триплетами Арканы (создаётся Alembic-миграцией, не runtime).
TABLE_TRIPLETS = "arcana_triplets"

_voyage_client = None


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
        # max_retries>0 включает нативный wait-and-retry SDK на rate-limit (429).
        # Дефолт 0 (не повторяет) — под бесплатный тир 3 RPM это критично.
        _voyage_client = voyageai.Client(api_key=key, max_retries=5)
        return _voyage_client
    except Exception as e:  # SDK не установлен / иная ошибка инициализации
        logger.warning("Voyage init failed: %s", e)
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
        # client.embed принимает СПИСОК → один запрос на весь батч; embeddings
        # возвращаются в порядке входных текстов.
        resp = client.embed(texts, model=VOYAGE_MODEL, input_type=input_type)
        return list(resp.embeddings or [])
    except Exception as e:
        if _is_rate_limit(e):
            # max_retries=5 в клиенте уже исчерпан → точку(и) пропускаем.
            logger.warning(
                "Voyage rate limit (3 RPM) исчерпан после ретраев — %s текст(ов) "
                "пропущено", len(texts),
            )
        else:
            logger.warning("Voyage embed failed (%s texts): %s", len(texts), e)
        return []


def _is_rate_limit(e: Exception) -> bool:
    """Это rate-limit ошибка Voyage (429)? Сначала по классу RateLimitError
    (если SDK его экспортирует), иначе по status_code==429 — на случай старого SDK."""
    try:
        from voyageai import RateLimitError
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass
    return getattr(e, "status_code", None) == 429


# ────────────────────────── pgvector helpers ───────────────────────────────

def _vec_literal(vec: List[float]) -> str:
    """list[float] → pgvector-текст '[v1,v2,...]'. Передаём text-параметром +
    CAST(:p AS vector) в SQL — без пакета pgvector и register_vector."""
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def _interp_excerpt(interpretation: str, limit: int = 400) -> str:
    """Короткий plain-text огрызок трактовки для инъекции «стиль/тон» —
    снимаем HTML-теги, схлопываем пробелы, режем по limit."""
    if not interpretation:
        return ""
    import re
    txt = re.sub(r"<[^>]+>", " ", interpretation)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:limit]


def _triplet_text(cards, question, interpretation) -> str:
    """Текст для эмбеддинга — непустые части {cards, question, interpretation}."""
    parts = [str(p).strip() for p in (cards, question, interpretation) if p and str(p).strip()]
    return " ".join(parts)


def _session_id(triplet_id) -> Optional[int]:
    """triplet_id (= id строки sessions для live) → int session_id, иначе None.
    None → у строки нет ключа upsert (live всегда даёт числовой id)."""
    s = str(triplet_id).strip()
    return int(s) if s.isdigit() else None


def _triplet_row(
    triplet_id, embedding, cards, question, interpretation,
    client_id, session_name, occurred_at,
) -> dict:
    """Bind-параметры одной строки INSERT (live-индексация). bottom_card/deck/
    triplet_summary заполняет только импорт истории — здесь NULL. source='live'."""
    cid = str(client_id).strip() if client_id else ""
    return {
        "session_id": _session_id(triplet_id),
        "embedding": _vec_literal(embedding),
        "client_id": int(cid) if cid.isdigit() else None,
        "session_name": session_name or None,
        "occurred_at": occurred_at or None,
        "question": question or None,
        "cards": cards or None,
        "interpretation": interpretation or None,
        "interp_excerpt": _interp_excerpt(interpretation),
    }


# upsert по session_id (переиндексация правки live-расклада). source/created_at на
# конфликте НЕ трогаем (created_at = когда впервые внесён; source остаётся).
_INSERT_SQL = sa.text(f"""
    INSERT INTO {TABLE_TRIPLETS}
        (session_id, embedding, client_id, session_name, occurred_at,
         question, cards, interpretation, interp_excerpt, source)
    VALUES
        (:session_id, CAST(:embedding AS vector), :client_id, :session_name,
         :occurred_at, :question, :cards, :interpretation, :interp_excerpt, 'live')
    ON CONFLICT (session_id) DO UPDATE SET
        embedding      = EXCLUDED.embedding,
        client_id      = EXCLUDED.client_id,
        session_name   = EXCLUDED.session_name,
        occurred_at    = EXCLUDED.occurred_at,
        question       = EXCLUDED.question,
        cards          = EXCLUDED.cards,
        interpretation = EXCLUDED.interpretation,
        interp_excerpt = EXCLUDED.interp_excerpt
""")


# ──────────────────────── store API (pgvector) ─────────────────────────────

def ensure_collection(
    name: str = TABLE_TRIPLETS, dim: int = VOYAGE_DIM, distance: Optional[object] = None
) -> bool:
    """Таблицу создаёт Alembic-миграция (не runtime). Здесь — проверка наличия.

    True если таблица существует, False — если нет/БД недоступна (graceful, без
    исключения). Сигнатура сохранена для совместимости вызовов."""
    if not name:
        return False
    try:
        with get_engine().connect() as conn:
            return bool(conn.execute(
                sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": name}
            ).scalar())
    except Exception as e:
        logger.warning("ensure_collection(%s) check failed: %s", name, e)
        return False


def index_triplet(
    triplet_id,
    cards: str,
    question: str,
    interpretation: str,
    client_id: Optional[str] = None,
    session_name: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> bool:
    """Индексирует ОДИН триплет в arcana_triplets (upsert по session_id). Один
    _embed = один запрос Voyage. Для N триплетов сессии — index_triplets_batch.

    Graceful: БД/Voyage недоступны → warning + False, без исключения."""
    text = _triplet_text(cards, question, interpretation)
    if not text:
        logger.warning("index_triplet(%s): пустой текст — пропуск", triplet_id)
        return False
    vecs = _embed(text, input_type="document")
    if not vecs:
        return False  # _embed уже залогировал (нет ключа / rate-limit / ошибка)
    try:
        row = _triplet_row(
            triplet_id, vecs[0], cards, question, interpretation,
            client_id, session_name, occurred_at,
        )
        with get_engine().begin() as conn:
            conn.execute(_INSERT_SQL, row)
        return True
    except Exception as e:
        logger.warning("index_triplet(%s) insert failed: %s", triplet_id, e)
        return False


def index_triplets_batch(triplets: List[dict]) -> int:
    """Индексирует N триплетов ОДНИМ запросом Voyage (батч-эмбеддинг) + одним
    executemany INSERT. Критично под лимит 3 RPM: N триплетов = 1 запрос Voyage.

    triplets: список dict с ключами triplet_id, cards, question, interpretation,
    client_id, session_name, occurred_at. Пустой текст — пропуск. Возвращает число
    проиндексированных строк. Graceful: 0 при недоступности."""
    # (исходный dict, текст) только для непустых — сохраняем выравнивание векторов.
    prepared = []
    for t in (triplets or []):
        text = _triplet_text(t.get("cards"), t.get("question"), t.get("interpretation"))
        if text:
            prepared.append((t, text))
    if not prepared:
        return 0
    vecs = _embed([txt for _, txt in prepared], input_type="document")
    if not vecs:
        return 0  # нет ключа / rate-limit / ошибка — _embed уже залогировал
    if len(vecs) != len(prepared):
        logger.warning(
            "index_triplets_batch: векторов %s != текстов %s — пропуск батча",
            len(vecs), len(prepared),
        )
        return 0
    rows = [
        _triplet_row(
            t["triplet_id"], vec, t.get("cards"), t.get("question"),
            t.get("interpretation"), t.get("client_id"),
            t.get("session_name"), t.get("occurred_at"),
        )
        for (t, _), vec in zip(prepared, vecs)
    ]
    try:
        with get_engine().begin() as conn:
            conn.execute(_INSERT_SQL, rows)  # список dict → executemany
        return len(rows)
    except Exception as e:
        logger.warning("index_triplets_batch insert failed: %s", e)
        return 0


def search_triplets(
    query_text: str, top_k: int = 5, client_id: Optional[str] = None
) -> List[dict]:
    """Семантический поиск похожих триплетов (косинус). Возвращает список dict с
    полями payload + score (контракт хендлера: triplet_id, interp_excerpt, cards).

    client_id задан → WHERE по client_id (история одного клиента); None → по ВСЕМ
    (консистентность голоса автора). Graceful: БД/Voyage недоступны → []."""
    if not query_text or not str(query_text).strip():
        return []
    vecs = _embed(query_text, input_type="query")
    if not vecs:
        return []
    try:
        params = {"q": _vec_literal(vecs[0]), "k": int(top_k)}
        where = ""
        cid = str(client_id).strip() if client_id else ""
        if cid.isdigit():
            where = "WHERE client_id = :client_id"
            params["client_id"] = int(cid)
        sql = sa.text(f"""
            SELECT session_id, client_id, session_name, occurred_at, question,
                   cards, bottom_card, deck, interp_excerpt,
                   1 - (embedding <=> CAST(:q AS vector)) AS score
            FROM {TABLE_TRIPLETS}
            {where}
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
        """)
        with get_engine().connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        out: List[dict] = []
        for r in rows:
            d = dict(r)
            sid = d.pop("session_id", None)
            # triplet_id (строка) — ID в pgvector-индексе
            d["triplet_id"] = str(sid) if sid is not None else None
            if d.get("occurred_at") is not None:
                d["occurred_at"] = str(d["occurred_at"])
            out.append(d)
        return out
    except Exception as e:
        logger.warning("search_triplets failed: %s", e)
        return []


def delete_triplet(triplet_id) -> bool:
    """Удаляет строку триплета из arcana_triplets (для правки/удаления расклада).
    Graceful: БД недоступна → False, без исключения."""
    sid = _session_id(triplet_id)
    if sid is None:
        return False
    try:
        with get_engine().begin() as conn:
            conn.execute(
                sa.text(f"DELETE FROM {TABLE_TRIPLETS} WHERE session_id = :sid"),
                {"sid": sid},
            )
        return True
    except Exception as e:
        logger.warning("delete_triplet(%s) failed: %s", triplet_id, e)
        return False
