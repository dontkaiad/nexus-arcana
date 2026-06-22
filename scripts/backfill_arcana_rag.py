"""scripts/backfill_arcana_rag.py — индексация всех существующих триплетов в Qdrant.

Батч-режим (#166): собираем тексты всех триплетов и эмбедим ПАЧКАМИ — один
запрос Voyage на чанк вместо N запросов (критично под бесплатный лимит 3 RPM).
Voyage принимает до 1000 текстов на запрос; чанкуем по 1000 и ждём 21с между
чанками (3 RPM = 1 запрос / 20с). Идемпотентно (upsert по детерминированному id).

Прогон вручную после деплоя:

    python -m scripts.backfill_arcana_rag

Требует поднятых Qdrant (коллекция arcana_triplets) и VOYAGE_API_KEY — иначе
index_triplets_batch graceful-no-op'ит (вернёт 0).
"""
from __future__ import annotations

import asyncio
import logging
import time

from arcana.repos.pg_sessions_repo import PgSessionsRepo
from core.rag import index_triplets_batch

logger = logging.getLogger("scripts.backfill_arcana_rag")

CHUNK = 1000          # лимит Voyage на запрос
THROTTLE_SEC = 21     # пауза между чанками под 3 RPM


async def backfill() -> int:
    """Индексирует все триплеты из PG пачками. Возвращает число проиндексированных."""
    repo = PgSessionsRepo()
    triplets = await repo.list_all()
    items = [
        {
            "triplet_id": t.id,
            "cards": t.cards,
            "question": t.question,
            "interpretation": t.interpretation,
            "client_id": t.client_id,
            "session_name": t.session_name,
            "occurred_at": t.date,
        }
        for t in triplets
    ]
    total = len(items)
    print(f"backfill: всего триплетов — {total}, чанков по {CHUNK}")
    done = 0
    for start in range(0, total, CHUNK):
        chunk = items[start:start + CHUNK]
        n = index_triplets_batch(chunk)  # ОДИН запрос Voyage на чанк
        done += n
        print(f"  чанк {start // CHUNK + 1}: {n}/{len(chunk)} (всего {done}/{total})",
              flush=True)
        # пауза только если впереди ещё чанк
        if start + CHUNK < total:
            time.sleep(THROTTLE_SEC)
    print(f"✅ backfill готов: {done}/{total} проиндексировано")
    return done


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(backfill())
