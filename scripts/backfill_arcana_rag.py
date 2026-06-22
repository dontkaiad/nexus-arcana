"""scripts/backfill_arcana_rag.py — индексация всех существующих триплетов в Qdrant.

Идемпотентно (index_triplet делает upsert по детерминированному id). Прогон
вручную после деплоя RAG-AB:

    python -m scripts.backfill_arcana_rag

Требует поднятых Qdrant (коллекция arcana_triplets) и VOYAGE_API_KEY — иначе
index_triplet graceful-no-op'ит, скрипт отработает но проиндексирует 0.
"""
from __future__ import annotations

import asyncio
import logging

from arcana.repos.pg_sessions_repo import PgSessionsRepo
from core.rag import index_triplet

logger = logging.getLogger("scripts.backfill_arcana_rag")


async def backfill() -> int:
    """Индексирует все триплеты из PG. Возвращает число успешно проиндексированных."""
    repo = PgSessionsRepo()
    triplets = await repo.list_all()
    total = len(triplets)
    print(f"backfill: всего триплетов — {total}")
    done = 0
    for i, t in enumerate(triplets, 1):
        try:
            ok = index_triplet(
                t.id, t.cards, t.question, t.interpretation,
                client_id=t.client_id, session_name=t.session_name,
                occurred_at=t.date,
            )
            if ok:
                done += 1
        except Exception as e:  # index_triplet и так graceful, но на всякий
            logger.warning("backfill id=%s failed: %s", t.id, e)
        print(f"  {i}/{total} (проиндексировано: {done}, id={t.id})", flush=True)
    print(f"✅ backfill готов: {done}/{total} проиндексировано")
    return done


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(backfill())
