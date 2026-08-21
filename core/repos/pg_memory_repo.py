"""core/repos/pg_memory_repo.py — PG implementation for 🧠 Память (ADR-0005)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, text, or_
from sqlalchemy.engine import Engine

from core.repos.memories_table import memories

logger = logging.getLogger("core.pg_memory_repo")

_engine: Optional[Engine] = None

_BOT_TO_SCOPE: Dict[str, str] = {
    "☀️ Nexus": "nexus",
    "🌒 Arcana": "arcana",
}
_SCOPE_TO_BOT: Dict[str, str] = {v: k for k, v in _BOT_TO_SCOPE.items()}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from arcana.repos.pg_sessions_repo import get_engine as _arc_engine
        _engine = _arc_engine()
    return _engine


def bot_to_scope(bot_label: str) -> str:
    return _BOT_TO_SCOPE.get(bot_label, "global")


def scope_to_bot(scope: str) -> str:
    return _SCOPE_TO_BOT.get(scope, "")


# ── Domain object ──────────────────────────────────────────────────────────────

@dataclass
class Memory:
    """Domain representation of one 🧠 Память row."""
    id: str
    fact: str
    key: str = ""
    value: str = ""
    category: str = ""
    scope: str = "global"    # "global" | "nexus" | "arcana"
    source: str = "manual"
    related_to: str = ""
    is_current: bool = True
    is_archived: bool = False
    user_notion_id: str = ""
    date: str = ""           # created_at[:10]
    updated_at: str = ""     # ISO string from updated_at column


# ── Row → domain object ───────────────────────────────────────────────────────

def _row_to_memory(row) -> Memory:
    created = getattr(row, "created_at", None)
    updated = getattr(row, "updated_at", None)
    date_str = created.date().isoformat() if created else ""
    updated_str = updated.isoformat() if updated else ""
    return Memory(
        id=str(row.id),
        fact=row.fact_text or "",
        key=row.key_name or "",
        value=row.value_text or "",
        category=row.category or "",
        scope=row.scope or "global",
        source=row.source or "manual",
        related_to=row.related_to or "",
        is_current=bool(row.is_current),
        is_archived=bool(row.is_archived),
        user_notion_id=row.user_notion_id or "",
        date=date_str,
        updated_at=updated_str,
    )


# ── Sync helpers ──────────────────────────────────────────────────────────────

def _add_sync(
    fact: str,
    key: str,
    category: str,
    scope: str,
    related_to: str,
    source: str,
    user_notion_id: str,
    notion_id: Optional[str] = None,
) -> str:
    with get_engine().begin() as conn:
        result = conn.execute(
            memories.insert().values(
                notion_id=notion_id,
                fact_text=fact,
                key_name=key or "",
                category=category or "",
                scope=scope or "global",
                source=source or "manual",
                related_to=related_to or "",
                is_current=True,
                is_archived=False,
                user_notion_id=user_notion_id or "",
            ).returning(memories.c.id)
        )
        return str(result.fetchone()[0])


def _upsert_sync(
    fact: str,
    key: str,
    category: str,
    scope: str,
    related_to: str,
    source: str,
    user_notion_id: str,
) -> Tuple[str, bool]:
    """Find existing by key+category, update; else create. Returns (id, was_updated)."""
    if key and category:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(memories.c.id)
                .where(memories.c.key_name == key)
                .where(memories.c.category == category)
                .where(memories.c.is_archived == False)  # noqa: E712
                .order_by(memories.c.created_at.desc())
                .limit(1)
            ).fetchone()
        if row:
            mem_id = row[0]
            with get_engine().begin() as conn:
                conn.execute(
                    memories.update()
                    .where(memories.c.id == mem_id)
                    .values(
                        fact_text=fact,
                        key_name=key,
                        category=category,
                        scope=scope or "global",
                        source=source or "manual",
                        related_to=related_to or "",
                        is_current=True,
                        user_notion_id=user_notion_id or "",
                        updated_at=text("now()"),
                    )
                )
            # Текст факта сменился → старый вектор ему больше не соответствует.
            # NULL-им отдельной транзакцией/try (#186, до переиндексации): если
            # эмбеддинг-колонки ещё нет (миграция не накатана) или БД капризит,
            # это НЕ должно откатить уже подтверждённое обновление факта выше.
            # Raw SQL: колонка embedding намеренно не описана в Table (см.
            # core/memory_rag.py). Небольшое окно гонки до переиндексации —
            # тот же trade-off, что и у всех остальных RAG-хуков в этом файле.
            try:
                with get_engine().begin() as conn:
                    conn.execute(
                        text("UPDATE memories SET embedding = NULL WHERE id = :mid"),
                        {"mid": mem_id},
                    )
            except Exception as e:
                logger.warning("memory upsert: embedding NULL-reset failed for %s: %s", mem_id, e)
            return str(mem_id), True
    mem_id = _add_sync(fact, key, category, scope, related_to, source, user_notion_id)
    return mem_id, False


def _update_fields_sync(
    memory_id: str,
    fact: Optional[str] = None,
    category: Optional[str] = None,
    related_to: Optional[str] = None,
) -> Optional[Tuple[str, str, str]]:
    """Точечная правка уже существующей строки ПО ID (не по key+category, в
    отличие от upsert) — используется reply-исправлением памяти (#188).
    Только переданные (не-None) поля меняются. Возвращает (fact, related_to,
    category) АКТУАЛЬНЫЕ после апдейта (для переиндексации) — None, если id
    не найден или нечего менять."""
    try:
        mid = int(memory_id)
    except (ValueError, TypeError):
        return None
    values: dict = {"updated_at": text("now()")}
    if fact is not None:
        values["fact_text"] = fact
    if category is not None:
        values["category"] = category
    if related_to is not None:
        values["related_to"] = related_to
    if len(values) == 1:  # только updated_at — нечего менять
        return None
    with get_engine().begin() as conn:
        result = conn.execute(
            memories.update().where(memories.c.id == mid).values(**values)
        )
        if result.rowcount == 0:
            return None
    with get_engine().connect() as conn:
        row = conn.execute(
            select(memories.c.fact_text, memories.c.related_to, memories.c.category)
            .where(memories.c.id == mid)
        ).fetchone()
    if not row:
        return None
    # Текст/категория сменились → старый вектор не соответствует. Тот же
    # trade-off, что в _upsert_sync: отдельная try/except-транзакция, чтобы
    # отсутствие embedding-колонки не откатило уже подтверждённую правку.
    try:
        with get_engine().begin() as conn:
            conn.execute(
                text("UPDATE memories SET embedding = NULL WHERE id = :mid"),
                {"mid": mid},
            )
    except Exception as e:
        logger.warning("memory update_fields: embedding NULL-reset failed for %s: %s", mid, e)
    return row[0] or "", row[1] or "", row[2] or ""


def _set_current_sync(memory_ids: List[str], is_current: bool) -> int:
    if not memory_ids:
        return 0
    ids_int = []
    for mid in memory_ids:
        try:
            ids_int.append(int(mid))
        except (ValueError, TypeError):
            pass
    if not ids_int:
        return 0
    with get_engine().begin() as conn:
        result = conn.execute(
            memories.update()
            .where(memories.c.id.in_(ids_int))
            .values(is_current=is_current, updated_at=text("now()"))
        )
    return result.rowcount


def _archive_sync(memory_id: str) -> bool:
    try:
        mid = int(memory_id)
        with get_engine().begin() as conn:
            conn.execute(
                memories.update()
                .where(memories.c.id == mid)
                .values(is_archived=True, updated_at=text("now()"))
            )
        return True
    except Exception as e:
        logger.error("archive %s failed: %s", memory_id, e)
        return False


def _base_active_q():
    return (
        select(memories)
        .where(memories.c.is_current == True)   # noqa: E712
        .where(memories.c.is_archived == False)  # noqa: E712
    )


def _search_sync(
    terms: List[str],
    scope: str = "",
    user_notion_id: str = "",
    page_size: int = 10,
) -> List[Memory]:
    if not terms:
        return []
    conditions = []
    for term in terms:
        like = f"%{term}%"
        conditions.append(memories.c.fact_text.ilike(like))
        conditions.append(memories.c.key_name.ilike(like))
        conditions.append(memories.c.related_to.ilike(like))
    q = _base_active_q().where(or_(*conditions))
    if scope and scope != "global":
        q = q.where(or_(memories.c.scope == scope, memories.c.scope == "global"))
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_by_category_sync(
    category: str,
    is_current: bool = True,
    scope: str = "",
    user_notion_id: str = "",
    page_size: int = 100,
) -> List[Memory]:
    q = (
        select(memories)
        .where(memories.c.is_archived == False)  # noqa: E712
        .where(memories.c.is_current == is_current)
    )
    if category:
        q = q.where(memories.c.category == category)
    if scope and scope != "global":
        q = q.where(or_(memories.c.scope == scope, memories.c.scope == "global"))
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_by_key_prefixes_sync(
    prefixes: List[str],
    user_notion_id: str = "",
) -> List[Memory]:
    """Find memories whose key_name starts with any of the given prefixes."""
    if not prefixes:
        return []
    conditions = [memories.c.key_name.ilike(f"{p}%") for p in prefixes]
    q = (
        _base_active_q()
        .where(or_(*conditions))
    )
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(500)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_by_exact_key_sync(
    key: str,
    user_notion_id: str = "",
    page_size: int = 1,
) -> List[Memory]:
    """Точный матч по key_name (==), не ilike. is_current=True, не архивирована."""
    q = (
        _base_active_q()
        .where(memories.c.key_name == key)
    )
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.updated_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


def _find_recent_sync(
    is_current: Optional[bool] = None,
    scope: str = "",
    user_notion_id: str = "",
    page_size: int = 10,
) -> List[Memory]:
    q = select(memories).where(memories.c.is_archived == False)  # noqa: E712
    if is_current is not None:
        q = q.where(memories.c.is_current == is_current)
    if scope and scope != "global":
        q = q.where(or_(memories.c.scope == scope, memories.c.scope == "global"))
    if user_notion_id:
        q = q.where(memories.c.user_notion_id == user_notion_id)
    q = q.order_by(memories.c.created_at.desc()).limit(page_size)
    with get_engine().connect() as conn:
        rows = conn.execute(q).fetchall()
    return [_row_to_memory(r) for r in rows]


# ── Embedding index hook (#186) ────────────────────────────────────────────────
# Индексация на уровне репо — ЕДИНСТВЕННАЯ точка, через которую проходят ВСЕ
# записи памяти (save_memory, save_parsed авто-подсказки, бюджетные upsert'ы
# finance, локация set_user_location). Fire-and-forget task: запись/ответ
# пользователю не ждут Voyage; сбой — warning + no-op (строка остаётся с
# embedding=NULL, подберёт backfill-скрипт).

_index_tasks: set = set()  # держим ссылки, иначе GC снимет незавершённый task


async def _index_embedding_safe(
    memory_id: str, fact: str, related_to: str, category: str
) -> None:
    try:
        # Лениво: core.memory_rag импортирует Memory из ЭТОГО модуля.
        from core import memory_rag
        embed_text = memory_rag._embed_text(fact, related_to, category)
        await asyncio.to_thread(memory_rag.index_memory, memory_id, embed_text)
    except Exception as e:
        logger.warning("memory embedding index failed for %s: %s", memory_id, e)


def _spawn_index(memory_id: str, fact: str, related_to: str, category: str) -> None:
    # add/upsert сами async — вызывающий loop всегда есть на момент вызова.
    # get_running_loop() тут не может бросить, но по-минимуму защищаемся —
    # без loop эта строка всё равно подберётся backfill-скриптом.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_index_embedding_safe(memory_id, fact, related_to, category))
    _index_tasks.add(task)
    task.add_done_callback(_index_tasks.discard)


# ── Public async API ───────────────────────────────────────────────────────────

class PgMemoryRepo:
    async def add(
        self,
        fact: str,
        key: str = "",
        category: str = "",
        scope: str = "global",
        related_to: str = "",
        source: str = "manual",
        user_notion_id: str = "",
        notion_id: Optional[str] = None,
    ) -> str:
        mem_id = await asyncio.to_thread(
            _add_sync, fact, key, category, scope, related_to, source, user_notion_id, notion_id
        )
        if mem_id:
            _spawn_index(mem_id, fact, related_to, category)
        return mem_id

    async def upsert(
        self,
        fact: str,
        key: str = "",
        category: str = "",
        scope: str = "global",
        related_to: str = "",
        source: str = "manual",
        user_notion_id: str = "",
    ) -> Tuple[str, bool]:
        mem_id, was_updated = await asyncio.to_thread(
            _upsert_sync, fact, key, category, scope, related_to, source, user_notion_id
        )
        if mem_id:
            _spawn_index(mem_id, fact, related_to, category)
        return mem_id, was_updated

    async def update_fields(
        self,
        memory_id: str,
        fact: Optional[str] = None,
        category: Optional[str] = None,
        related_to: Optional[str] = None,
    ) -> bool:
        """Точечная правка по id (reply-исправление, #188) — НЕ upsert (тот
        матчит по key+category, тут правим конкретную уже известную строку).
        Только переданные поля меняются. Переиндексирует эмбеддинг."""
        fresh = await asyncio.to_thread(
            _update_fields_sync, memory_id, fact, category, related_to
        )
        if fresh is None:
            return False
        fresh_fact, fresh_related, fresh_category = fresh
        _spawn_index(memory_id, fresh_fact, fresh_related, fresh_category)
        return True

    async def set_current(self, memory_ids: List[str], is_current: bool) -> int:
        return await asyncio.to_thread(_set_current_sync, memory_ids, is_current)

    async def archive(self, memory_id: str) -> bool:
        return await asyncio.to_thread(_archive_sync, memory_id)

    async def search(
        self,
        terms: List[str],
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 10,
    ) -> List[Memory]:
        return await asyncio.to_thread(_search_sync, terms, scope, user_notion_id, page_size)

    async def find_by_category(
        self,
        category: str,
        is_current: bool = True,
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 100,
    ) -> List[Memory]:
        return await asyncio.to_thread(
            _find_by_category_sync, category, is_current, scope, user_notion_id, page_size
        )

    async def find_by_key_prefixes(
        self,
        prefixes: List[str],
        user_notion_id: str = "",
    ) -> List[Memory]:
        return await asyncio.to_thread(_find_by_key_prefixes_sync, prefixes, user_notion_id)

    async def find_by_exact_key(
        self,
        key: str,
        user_notion_id: str = "",
        page_size: int = 1,
    ) -> List[Memory]:
        return await asyncio.to_thread(_find_by_exact_key_sync, key, user_notion_id, page_size)

    async def find_recent(
        self,
        is_current: Optional[bool] = None,
        scope: str = "",
        user_notion_id: str = "",
        page_size: int = 10,
    ) -> List[Memory]:
        return await asyncio.to_thread(
            _find_recent_sync, is_current, scope, user_notion_id, page_size
        )
