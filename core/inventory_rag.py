"""core/inventory_rag.py — семантический поиск по 📦 Инвентарю Nexus
(Voyage эмбеддинги + pgvector).

Тот же подход, что `core/memory_rag.py`: embedding — колонка в `nexus_lists`
(`alembic/versions/h8i9j0k1l2m3_nexus_lists_embedding_pgvector.py`), Table в
`lists_table.py` про неё не знает — только raw SQL здесь. Эмбеддинг-клиент
общий (`core.rag._embed`, один 3 RPM бюджет на всех RAG-потребителей).

Зачем: ILIKE по названию не находит «что-то от похмелья» → «Алка-Зельцер».
Здесь вектор строится из name + category + note, а Haiku-реранк отсекает
нерелевантное (cosine distance сам по себе не отделяет «нет такого» от
«есть»; пустой результат реранка = честное «не найдено»).

Graceful, как и остальной RAG: нет ключа / БД / pgvector → warning и
пустой результат, поиск остаётся на чистом ILIKE.
"""
from __future__ import annotations

import json
import logging
from typing import List

import sqlalchemy as sa

from core.claude_client import ask_claude
from core.db import get_engine
from core.rag import _embed, _vec_literal
from core.repos.lists_table import nexus_lists
from core.repos.pg_nexus_lists_repo import ListItem, _row_to_list_item

logger = logging.getLogger("core.inventory_rag")

TABLE = "nexus_lists"
INVENTORY_TYPE = "инвентарь"  # значение list_type в PG (см. _pg_type)
DEFAULT_TOP_K = 10

_RERANK_SYSTEM = (
    "Ты помогаешь искать в домашнем инвентаре (аптечка, бытовые вещи, "
    "продукты). Тебе даны запрос пользователя и список позиций с полями "
    "id/название/категория/заметка. Выбери позиции, которые РЕАЛЬНО "
    "подходят под запрос по смыслу или назначению (например, на запрос "
    "«от похмелья» подходят сорбенты и средства от тошноты/головной боли, "
    "а не любые таблетки). Если подходящих нет — верни []. "
    "Верни ТОЛЬКО JSON-массив id, без текста и markdown."
)


def embed_text(name: str, category: str = "", note: str = "") -> str:
    """Текст для эмбеддинга позиции — непустые {name, category, note}."""
    parts = [str(p).strip() for p in (name, category, note) if p and str(p).strip()]
    return " ".join(parts)


def index_item(item_id: str, text: str) -> bool:
    """Индексирует ОДНУ позицию. Graceful: False при любой проблеме."""
    if not text:
        return False
    vecs = _embed(text, input_type="document")
    if not vecs:
        return False
    try:
        with get_engine().begin() as conn:
            conn.execute(
                sa.text(f"UPDATE {TABLE} SET embedding = CAST(:e AS vector) WHERE id = :id"),
                {"e": _vec_literal(vecs[0]), "id": int(item_id)},
            )
        return True
    except Exception as e:
        logger.warning("index_item(%s) update failed: %s", item_id, e)
        return False


def index_items_batch(items: List[dict]) -> int:
    """N позиций = один запрос Voyage (под 3 RPM). items:
    [{"item_id": ..., "embed_text": ...}]. Возвращает число записанных."""
    prepared = [it for it in (items or []) if it.get("embed_text")]
    if not prepared:
        return 0
    vecs = _embed([it["embed_text"] for it in prepared], input_type="document")
    if not vecs or len(vecs) != len(prepared):
        if vecs:
            logger.warning(
                "index_items_batch: векторов %s != текстов %s — пропуск",
                len(vecs), len(prepared),
            )
        return 0
    try:
        with get_engine().begin() as conn:
            for it, vec in zip(prepared, vecs):
                conn.execute(
                    sa.text(f"UPDATE {TABLE} SET embedding = CAST(:e AS vector) WHERE id = :id"),
                    {"e": _vec_literal(vec), "id": int(it["item_id"])},
                )
        return len(prepared)
    except Exception as e:
        logger.warning("index_items_batch update failed: %s", e)
        return 0


def reset_embedding(item_id: str) -> None:
    """Текст позиции сменился → старый вектор недействителен."""
    try:
        with get_engine().begin() as conn:
            conn.execute(
                sa.text(f"UPDATE {TABLE} SET embedding = NULL WHERE id = :id"),
                {"id": int(item_id)},
            )
    except Exception as e:
        logger.warning("reset_embedding(%s) failed: %s", item_id, e)


def search_inventory_semantic(
    query: str, user_id: str = "", top_k: int = DEFAULT_TOP_K,
) -> List[ListItem]:
    """Top-K ближайших позиций инвентаря (не archived) по косинусу.
    Graceful: ошибка / нет эмбеддинга запроса → []."""
    if not query or not str(query).strip():
        return []
    vecs = _embed(query, input_type="query")
    if not vecs:
        return []
    try:
        stmt = (
            sa.select(
                nexus_lists,
                sa.literal_column("1 - (embedding <=> CAST(:q AS vector))").label("score"),
            )
            .where(nexus_lists.c.list_type == INVENTORY_TYPE)
            .where(nexus_lists.c.status != "archived")
            .where(sa.text("embedding IS NOT NULL"))
        )
        if user_id:
            stmt = stmt.where(nexus_lists.c.user_id == user_id)
        stmt = stmt.order_by(sa.text("embedding <=> CAST(:q AS vector)")).limit(int(top_k))
        with get_engine().connect() as conn:
            rows = conn.execute(stmt, {"q": _vec_literal(vecs[0])}).fetchall()
        logger.info(
            "search_inventory_semantic: query=%r ids=%s scores=%s",
            query[:60], [r.id for r in rows], [round(float(r.score), 3) for r in rows],
        )
        return [_row_to_list_item(r) for r in rows]
    except Exception as e:
        logger.warning("search_inventory_semantic failed: %s", e)
        return []


def _candidate_line(it: ListItem) -> str:
    parts = [f"id={it.id}", f'название="{it.name}"']
    if it.category:
        parts.append(f'категория="{it.category}"')
    if it.note:
        parts.append(f'заметка="{it.note}"')
    return " ".join(parts)


async def rerank_inventory_candidates(query: str, candidates: List[ListItem]) -> List[ListItem]:
    """Haiku оставляет релевантное запросу подмножество. Ошибка / невалидный
    JSON → [] (вызывающий покажет обычное «не найден»)."""
    if not candidates:
        return []
    prompt = (
        f"Запрос пользователя: {query!r}\n\nПозиции:\n"
        + "\n".join(_candidate_line(c) for c in candidates)
    )
    try:
        raw = await ask_claude(
            prompt=prompt,
            system=_RERANK_SYSTEM,
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0,
        )
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        ids = json.loads(cleaned)
        if not isinstance(ids, list):
            raise ValueError("rerank JSON is not a list")
    except Exception as e:
        logger.warning("rerank_inventory_candidates failed: %s", e)
        return []
    wanted = {str(i) for i in ids}
    return [c for c in candidates if c.id in wanted]
