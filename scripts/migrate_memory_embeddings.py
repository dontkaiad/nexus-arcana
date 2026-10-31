"""scripts/migrate_memory_embeddings.py — бэкфилл эмбеддингов 🧠 Память (#184).

Что делает: для строк `memories` без embedding (`embedding IS NULL AND
is_archived = false`) генерирует Voyage-эмбеддинг (`core.memory_rag`) и
пишет в колонку `embedding` — та же логика текста, что и live-индексация
(`_embed_text`: только факт; для факта человека — с именем, без алиасов и категории).
Tombstone-строки (`fact_text = ''`) пропускаются.

`--all` — ПЕРЕсчёт всех активных строк (смена формата эмбеддинг-текста). Перед
записью делает бэкап `memories_embedding_bak(id, embedding)`; откат —
`UPDATE memories m SET embedding = b.embedding FROM memories_embedding_bak b WHERE b.id = m.id`.
Dry-run печатает число строк, оценку токенов и стоимость.

Идемпотентность: повторный прогон no-op для уже проиндексированных строк
(фильтр `embedding IS NULL` в самом запросе).

Использование::

    # Безопасный dry-run (по умолчанию): count + 3 примера + оценка Voyage-вызовов
    python3 scripts/migrate_memory_embeddings.py
    python3 scripts/migrate_memory_embeddings.py --limit 200

    # Реальная запись (только с явного go apply от Кай — правит прод-Postgres)
    python3 scripts/migrate_memory_embeddings.py --apply --limit 50

CLI:
    --dry-run     (default): count + 3 сэмпла (только id, категория, длина — БЕЗ содержимого записей:
                             в памяти лежат личные данные) + оценка запросов, без записи
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

# Пауза между батчами (сек). Лимит RPM зависит от тарифа ключа: без привязанной
# карты — единицы RPM (тогда ставь --sleep 21), с картой достаточно ~0.5с.
_DEFAULT_SLEEP_SEC = 0.5
# voyage-4-lite: ~$0.02 / 1M токенов (первые 200M бесплатно). Кириллица ≈ 3 симв./токен.
_USD_PER_MTOK = 0.02
_CHARS_PER_TOKEN = 3.0


def _fetch_unindexed(limit: Optional[int], reindex_all: bool = False) -> List[dict]:
    where = "is_archived = false AND fact_text <> ''"
    if not reindex_all:
        where += " AND embedding IS NULL"
    sql = f"""
        SELECT id, fact_text, related_to, category, person_id
        FROM memories
        WHERE {where}
        ORDER BY id
    """
    if limit:
        sql += " LIMIT :limit"
    with get_engine().connect() as conn:
        rows = conn.execute(sa.text(sql), {"limit": limit} if limit else {}).fetchall()
    return [
        {
            "memory_id": str(r.id),
            "embed_text": _embed_text(r.fact_text, r.related_to, r.category, getattr(r, "person_id", None)),
            # только метаданные для вывода: само содержимое записи в консоль не попадает
            "category": r.category or "",
            "length": len(r.fact_text or ""),
        }
        for r in rows
    ]


def _chunks(items: List[dict], size: int) -> List[List[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def estimate(items: List[dict]) -> dict:
    chars = sum(len(it["embed_text"]) for it in items)
    tokens = int(chars / _CHARS_PER_TOKEN) + len(items)
    return {"tokens": tokens, "usd": tokens / 1_000_000 * _USD_PER_MTOK}


def _backup_embeddings() -> None:
    with get_engine().begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS memories_embedding_bak"))
        conn.execute(sa.text(
            "CREATE TABLE memories_embedding_bak AS SELECT id, embedding FROM memories"))


def run(limit: Optional[int], apply: bool, batch_size: int,
        reindex_all: bool = False, sleep_sec: float = _DEFAULT_SLEEP_SEC) -> dict:
    items = [it for it in _fetch_unindexed(limit, reindex_all) if it["embed_text"]]
    summary = {"scanned": len(items), "applied": 0, "sample": items[:3], **estimate(items)}
    if not items:
        return summary
    if apply and reindex_all:
        _backup_embeddings()
        print("backup: memories_embedding_bak создана")

    for i, chunk in enumerate(_chunks(items, batch_size), start=1):
        print(f"[{i}] batch of {len(chunk)} — action={'APPLY' if apply else 'DRY'}")
        if apply:
            n = index_memories_batch(chunk)
            summary["applied"] += n
            if i < len(_chunks(items, batch_size)):
                time.sleep(sleep_sec)
    return summary


def format_sample(item: dict) -> str:
    """Строка сэмпла: id, категория, длина факта. Содержимое записи НЕ печатаем."""
    return f"id={item['memory_id']} category={item.get('category') or '—'} len={item.get('length', '?')}"


def _print_summary(summary: dict, apply: bool, batch_size: int, sleep_sec: float = _DEFAULT_SLEEP_SEC) -> None:
    print()
    print("=" * 60)
    print(f"  SCANNED (без эмбеддинга): {summary['scanned']}")
    if apply:
        print(f"  APPLIED:                  {summary['applied']}")
    else:
        est_calls = (summary["scanned"] + batch_size - 1) // batch_size if summary["scanned"] else 0
        print(f"  APPLIED:                  0 (dry-run, --apply для записи)")
        print(f"  ОЦЕНКА Voyage-вызовов:    {est_calls} (батч={batch_size}, ~{sleep_sec:g}с между)")
        if "tokens" in summary:
            print(f"  ОЦЕНКА токенов / $:       ~{summary['tokens']} / ${summary['usd']:.5f}")
    print("=" * 60)
    if summary["sample"]:
        print("\nSAMPLE (first 3; только id / категория / длина, без содержимого):")
        for s in summary["sample"]:
            print(f"  {format_sample(s)}")


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
    parser.add_argument("--all", action="store_true",
                        help="Пересчитать ВСЕ активные строки (с бэкапом memories_embedding_bak)")
    parser.add_argument("--sleep", type=float, default=_DEFAULT_SLEEP_SEC,
                        help="Пауза между батчами, сек (21 если ключ без карты)")
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

    summary = run(args.limit, apply, args.batch_size, reindex_all=args.all, sleep_sec=args.sleep)
    _print_summary(summary, apply, args.batch_size, args.sleep)


if __name__ == "__main__":
    main()
