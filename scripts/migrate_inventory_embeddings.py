"""scripts/migrate_inventory_embeddings.py — бэкфилл эмбеддингов 📦 Инвентаря Nexus.

Для всех позиций инвентаря без embedding (`embedding IS NULL`, не archived)
генерирует Voyage-эмбеддинг (`core.inventory_rag`) и пишет в `nexus_lists.embedding`.
Идемпотентно: повторный прогон пропускает уже проиндексированные.

    python3 scripts/migrate_inventory_embeddings.py              # dry-run
    python3 scripts/migrate_inventory_embeddings.py --apply      # запись в Postgres

CLI: --apply, --limit N, --batch-size N (default 20 строк на один Voyage-запрос).
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
from core.inventory_rag import INVENTORY_TYPE, embed_text, index_items_batch  # noqa: E402

# Voyage free tier — 3 RPM.
_RATE_LIMIT_SEC = 21.0


def _fetch_unindexed(limit: Optional[int]) -> List[dict]:
    sql = """
        SELECT id, name, category, note
        FROM nexus_lists
        WHERE list_type = :lt AND status != 'archived' AND embedding IS NULL
        ORDER BY id
    """
    params: dict = {"lt": INVENTORY_TYPE}
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit
    with get_engine().connect() as conn:
        rows = conn.execute(sa.text(sql), params).fetchall()
    return [
        {"item_id": str(r.id), "embed_text": embed_text(r.name, r.category, r.note)}
        for r in rows
    ]


def run(limit: Optional[int], apply: bool, batch_size: int) -> dict:
    items = [it for it in _fetch_unindexed(limit) if it["embed_text"]]
    summary = {"scanned": len(items), "applied": 0, "sample": items[:3]}
    chunks = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    for i, chunk in enumerate(chunks, start=1):
        print(f"[{i}/{len(chunks)}] batch of {len(chunk)} — {'APPLY' if apply else 'DRY'}")
        if apply:
            summary["applied"] += index_items_batch(chunk)
            if i < len(chunks):
                time.sleep(_RATE_LIMIT_SEC)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл эмбеддингов 📦 Инвентаря Nexus")
    parser.add_argument("--apply", action="store_true", help="Реальная запись (по умолчанию dry-run)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.apply:
        print("⚠️ APPLY MODE — пишу в Postgres (nexus_lists.embedding). Ctrl+C в первые 3с чтоб отменить.")
        time.sleep(3)

    summary = run(args.limit, args.apply, args.batch_size)
    print(f"\nSCANNED: {summary['scanned']}  APPLIED: {summary['applied']}")
    for s in summary["sample"]:
        print(f"  id={s['item_id']}: {s['embed_text'][:120]!r}")


if __name__ == "__main__":
    main()
