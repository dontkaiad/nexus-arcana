"""scripts/migrate_memory_embeddings.py — бэкфилл эмбеддингов 🧠 Память (#184).

Что делает: для всех строк `memories` без embedding (`embedding IS NULL AND
is_archived = false`) генерирует Voyage-эмбеддинг (`core.memory_rag`) и
пишет в колонку `embedding` — та же логика текста, что и live-индексация
в `core/memory.py:_rag_index_memory_safe` (fact + related_to + category).

Идемпотентность: повторный прогон no-op для уже проиндексированных строк
(фильтр `embedding IS NULL` в самом запросе).

Использование::

    # Безопасный dry-run (по умолчанию): count + 3 примера + оценка Voyage-вызовов
    python3 scripts/migrate_memory_embeddings.py
    python3 scripts/migrate_memory_embeddings.py --limit 200

    # Реальная запись (только с явного go apply от Кай — правит прод-Postgres)
    python3 scripts/migrate_memory_embeddings.py --apply --limit 50

CLI:
    --dry-run     (default): count + 3 сэмпла embed_text + оценка запросов, без записи
    --apply                  (опасно): реально пишет embedding в Postgres
    --limit N                (default: без лимита) сколько строк просканировать
    --batch-size N            (default 20) строк на один Voyage-запрос
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa  # noqa: E402

from core.db import get_engine  # noqa: E402
from core.memory_rag import _embed_text, index_memories_batch  # noqa: E402

logger = logging.getLogger("migrate_memory_embeddings")

# Voyage free tier — 3 RPM. batch_size строк = 1 запрос; между батчами спим.
_RATE_LIMIT_SEC = 21.0


def _fetch_unindexed(limit: Optional[int]) -> List[dict]:
    sql = """
        SELECT id, fact_text, related_to, category
        FROM memories
        WHERE embedding IS NULL AND is_archived = false
        ORDER BY id
    """
    if limit:
        sql += " LIMIT :limit"
    with get_engine().connect() as conn:
        rows = conn.execute(sa.text(sql), {"limit": limit} if limit else {}).fetchall()
    return [
        {
            "memory_id": str(r.id),
            "embed_text": _embed_text(r.fact_text, r.related_to, r.category),
        }
        for r in rows
    ]


def _chunks(items: List[dict], size: int) -> List[List[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def run(limit: Optional[int], apply: bool, batch_size: int) -> dict:
    items = [it for it in _fetch_unindexed(limit) if it["embed_text"]]
    summary = {"scanned": len(items), "applied": 0, "sample": items[:3]}
    if not items:
        return summary

    for i, chunk in enumerate(_chunks(items, batch_size), start=1):
        print(f"[{i}] batch of {len(chunk)} — action={'APPLY' if apply else 'DRY'}")
        if apply:
            n = index_memories_batch(chunk)
            summary["applied"] += n
            if i < len(_chunks(items, batch_size)):
                time.sleep(_RATE_LIMIT_SEC)
    return summary


def _print_summary(summary: dict, apply: bool, batch_size: int) -> None:
    print()
    print("=" * 60)
    print(f"  SCANNED (без эмбеддинга): {summary['scanned']}")
    if apply:
        print(f"  APPLIED:                  {summary['applied']}")
    else:
        est_calls = (summary["scanned"] + batch_size - 1) // batch_size if summary["scanned"] else 0
        print(f"  APPLIED:                  0 (dry-run, --apply для записи)")
        print(f"  ОЦЕНКА Voyage-вызовов:    {est_calls} (батч={batch_size}, ~{_RATE_LIMIT_SEC:.0f}с между)")
    print("=" * 60)
    if summary["sample"]:
        print("\nSAMPLE (first 3 embed_text):")
        for s in summary["sample"]:
            print(f"  id={s['memory_id']}: {s['embed_text'][:120]!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл эмбеддингов 🧠 Память (#184)")
    parser.add_argument("--apply", action="store_true",
                        help="Реальная запись embedding в Postgres (по умолчанию dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет сделано (default)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Сколько строк просканировать (default: без лимита)")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="Строк на один Voyage-запрос (default 20)")
    args = parser.parse_args()

    apply = bool(args.apply)
    if apply and args.dry_run:
        print("⚠️ --apply и --dry-run одновременно — apply отменён, делаю dry-run")
        apply = False

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if apply:
        print("⚠️ APPLY MODE — пишу в продовый Postgres (memories.embedding). "
              "Ctrl+C в первые 3с чтоб отменить.")
        time.sleep(3)

    summary = run(args.limit, apply, args.batch_size)
    _print_summary(summary, apply, args.batch_size)


if __name__ == "__main__":
    main()
