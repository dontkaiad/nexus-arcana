"""core/memory_rag.py — RAG-поиск по 🧠 Память (Voyage эмбеддинги + pgvector, #184).

Вторая точка использования RAG-инфраструктуры из `core/rag.py` (первая —
`arcana_triplets`, только Arcana). Эмбеддинг-клиент и низкоуровневые
хелперы (`_embed`, `_vec_literal`, Voyage-модель/dim) переиспользуются ИЗ
`core.rag` напрямую — не дублируются, это один и тот же Voyage-клиент с
общим 3 RPM free-tier бюджетом на ОБА RAG-потребителя.

Отличие от `core/rag.py`: там своя таблица `arcana_triplets` (derived
read-model над sessions, отдельный upsert по session_id). Здесь —
embedding живёт КАК КОЛОНКА в самой таблице `memories`
(`alembic/versions/y5z6a7b8c9d0_memories_embedding_pgvector.py`), потому
что memory-запись уже атомарна (одна строка = один факт), и колонка не
рискует рассинхроном как отдельная зеркальная таблица.

`core/repos/memories_table.py` (SQLAlchemy Core `Table` для CRUD в
`pg_memory_repo.py`) НЕ знает про колонку `embedding` — она видна только
через raw SQL здесь, так же как `core/rag.py` работает с `arcana_triplets`
без пакета `pgvector`/`register_vector`.

Принципы — те же, что в `core/rag.py`: всё graceful (нет ключа / БД
недоступна / pgvector не стоит → warning в лог + пусто/no-op, без
исключений — боты не падают, память работает без RAG на чистом ILIKE).
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

import sqlalchemy as sa

from core.claude_client import ask_claude
from core.db import get_engine
from core.rag import _embed, _vec_literal
from core.repos.pg_memory_repo import Memory

logger = logging.getLogger("core.memory_rag")

TABLE_MEMORIES = "memories"

# Дефолт top_k для search_memory_semantic (ADR-0021): шире сеть кандидатов
# от pgvector, т.к. на коротких фактах правильный ответ не всегда топ-5 по
# чистому cosine distance — реранк ниже отбирает смысл, не расстояние.
DEFAULT_TOP_K = 10

_RERANK_SYSTEM = (
    "Ты фильтруешь кандидатов семантического поиска по личным заметкам "
    "пользователя (🧠 Память). Тебе даны запрос и список кандидатов с "
    "полями id/факт/категория/связь. Категория и связь — это контекст, "
    "которого не хватает эмбеддингу на коротких фактах: например категория "
    "«🐾 Коты» рядом со словом «луна» явно указывает на кличку питомца, а "
    "не на слово «луна» в бытовом или ином смысле.\n"
    "Верни ТОЛЬКО JSON-массив id тех кандидатов, что РЕАЛЬНО релевантны "
    "запросу по смыслу. Если релевантных нет — верни []. Никакого текста "
    "до или после JSON, никакого markdown."
)


def _embed_text(fact: str, related_to: str = "", category: str = "") -> str:
    """Текст для эмбеддинга факта — непустые части {fact, related_to,
    category} через пробел. category/related_to добавляют сигнал (напр.
    факт про место находится лучше, если вектор кодирует и категорию)."""
    parts = [str(p).strip() for p in (fact, related_to, category) if p and str(p).strip()]
    return " ".join(parts)


def index_memory(memory_id: str, embed_text: str) -> bool:
    """Индексирует ОДНУ память (эмбеддинг + UPDATE embedding колонки).
    Graceful: пустой текст / нет ключа / ошибка БД → False, без исключения."""
    if not embed_text:
        logger.warning("index_memory(%s): пустой текст — пропуск", memory_id)
        return False
    vecs = _embed(embed_text, input_type="document")
    if not vecs:
        return False  # _embed уже залогировал (нет ключа / rate-limit / ошибка)
    try:
        with get_engine().begin() as conn:
            conn.execute(
                sa.text(
                    f"UPDATE {TABLE_MEMORIES} SET embedding = CAST(:e AS vector) "
                    "WHERE id = :id"
                ),
                {"e": _vec_literal(vecs[0]), "id": int(memory_id)},
            )
        return True
    except Exception as e:
        logger.warning("index_memory(%s) update failed: %s", memory_id, e)
        return False


def index_memories_batch(items: List[dict]) -> int:
    """Индексирует N памятей ОДНИМ запросом Voyage (батч-эмбеддинг) — под
    лимит 3 RPM: N памятей = 1 запрос Voyage. Используется бэкфиллом.

    items: [{"memory_id": ..., "embed_text": ...}, ...]. Пустой текст —
    пропуск. Возвращает число проиндексированных строк. Graceful: 0 при
    недоступности."""
    prepared = [it for it in (items or []) if it.get("embed_text")]
    if not prepared:
        return 0
    vecs = _embed([it["embed_text"] for it in prepared], input_type="document")
    if not vecs:
        return 0
    if len(vecs) != len(prepared):
        logger.warning(
            "index_memories_batch: векторов %s != текстов %s — пропуск батча",
            len(vecs), len(prepared),
        )
        return 0
    try:
        with get_engine().begin() as conn:
            for it, vec in zip(prepared, vecs):
                conn.execute(
                    sa.text(
                        f"UPDATE {TABLE_MEMORIES} SET embedding = CAST(:e AS vector) "
                        "WHERE id = :id"
                    ),
                    {"e": _vec_literal(vec), "id": int(it["memory_id"])},
                )
        return len(prepared)
    except Exception as e:
        logger.warning("index_memories_batch update failed: %s", e)
        return 0


def search_memory_semantic(
    query_text: str,
    scope: str = "",
    user_notion_id: str = "",
    top_k: int = DEFAULT_TOP_K,
    min_score: Optional[float] = None,
) -> List[Memory]:
    """Семантический поиск похожих memory-фактов (косинус). Возвращает
    `Memory` (тот же тип, что `PgMemoryRepo`) для прямого мержа с
    ILIKE-результатами вызывающей стороной.

    scope задан → доп. фильтр (scope=:scope OR scope='global'); пусто →
    без фильтра scope (как текущий ILIKE-путь). Только is_current и не
    is_archived. Graceful: БД/Voyage недоступны → [].

    min_score (#185, ADR-0021): на реальных данных top-1 по cosine distance
    не всегда топ-1 по смыслу (короткие факты вроде «в Алании» эмбеддятся
    слишком обще, «луна» путается со словом и с кличкой кота) — порог
    расстояния тут не разделяет релевантное от нерелевантного, поэтому
    остаётся отключённым (None) по умолчанию. Отбор смысла делает
    `rerank_memory_candidates` НАД top_k результатами этой функции, а не
    сама эта функция. min_score остаётся доступным параметром на случай,
    если понадобится совсем грубый предфильтр перед реранком."""
    if not query_text or not str(query_text).strip():
        return []
    vecs = _embed(query_text, input_type="query")
    if not vecs:
        return []
    try:
        params = {"q": _vec_literal(vecs[0]), "k": int(top_k)}
        where_extra = ""
        if scope:
            where_extra += " AND (scope = :scope OR scope = 'global')"
            params["scope"] = scope
        if user_notion_id:
            where_extra += " AND user_notion_id = :uid"
            params["uid"] = user_notion_id
        sql = sa.text(f"""
            SELECT id, fact_text, key_name, value_text, category, scope, source,
                   related_to, is_current, is_archived, user_notion_id,
                   created_at, updated_at,
                   1 - (embedding <=> CAST(:q AS vector)) AS score
            FROM {TABLE_MEMORIES}
            WHERE embedding IS NOT NULL
              AND is_archived = false
              AND is_current = true
              {where_extra}
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :k
        """)
        with get_engine().connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        # Сырое распределение score — данные для калибровки порога (#185),
        # НЕ фильтр по умолчанию (min_score=None -> ничего не отсекаем).
        if rows:
            ids = [dict(r).get("id") for r in rows]
            scores = [dict(r).get("score") for r in rows]
            logger.info(
                "search_memory_semantic: query=%r ids=%s scores=%s min_score=%s",
                query_text[:60], ids,
                [round(s, 3) if s is not None else None for s in scores],
                min_score,
            )
        out: List[Memory] = []
        for r in rows:
            d = dict(r)
            score = d.get("score")
            if min_score is not None and score is not None and score < min_score:
                continue
            created = d.get("created_at")
            updated = d.get("updated_at")
            out.append(Memory(
                id=str(d["id"]),
                fact=d.get("fact_text") or "",
                key=d.get("key_name") or "",
                value=d.get("value_text") or "",
                category=d.get("category") or "",
                scope=d.get("scope") or "global",
                source=d.get("source") or "manual",
                related_to=d.get("related_to") or "",
                is_current=bool(d.get("is_current")),
                is_archived=bool(d.get("is_archived")),
                user_notion_id=d.get("user_notion_id") or "",
                date=created.date().isoformat() if created else "",
                updated_at=updated.isoformat() if updated else "",
            ))
        return out
    except Exception as e:
        logger.warning("search_memory_semantic failed: %s", e)
        return []


def _rerank_candidate_line(m: Memory) -> str:
    parts = [f"id={m.id}", f'факт="{m.fact}"']
    if m.category:
        parts.append(f'категория="{m.category}"')
    if m.related_to:
        parts.append(f'связь="{m.related_to}"')
    return " ".join(parts)


async def rerank_memory_candidates(query: str, candidates: List[Memory]) -> List[Memory]:
    """Haiku-реранк top-K кандидатов pgvector (ADR-0021, #185): cosine
    distance топ-1 не гарантирует смысловую релевантность — general-purpose
    эмбеддинг не различает «луна-слово» и «Луна-кличка кота». category/
    related_to дают Haiku контекст, которого не хватает самому эмбеддингу.

    Возвращает подмножество candidates, которое Haiku пометил релевантным
    запросу (порядок как в candidates). Graceful: пустой список кандидатов,
    ошибка Haiku или невалидный JSON в ответе → [] — вызывающая сторона
    (core/memory.py) в этом случае просто не добавляет semantic-довесок,
    без сообщения-заглушки."""
    if not candidates:
        return []
    prompt = (
        f"Запрос пользователя: {query!r}\n\nКандидаты:\n"
        + "\n".join(_rerank_candidate_line(m) for m in candidates)
    )
    raw = await ask_claude(
        prompt=prompt,
        system=_RERANK_SYSTEM,
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        temperature=0,
    )
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        ids = json.loads(cleaned)
        if not isinstance(ids, list):
            raise ValueError("rerank JSON is not a list")
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        logger.warning("rerank_memory_candidates: bad JSON from Haiku: %r (%s)", raw, e)
        return []
    relevant_ids = {str(i) for i in ids}
    relevant = [m for m in candidates if m.id in relevant_ids]
    logger.info(
        "rerank_memory_candidates: query=%r candidate_ids=%s relevant_ids=%s",
        query[:60], [m.id for m in candidates], [m.id for m in relevant],
    )
    return relevant
