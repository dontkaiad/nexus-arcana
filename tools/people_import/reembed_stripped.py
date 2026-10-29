"""Пересчёт эмбеддингов у фактов людей, у которых миграция срезала префикс «Имя: » (очередь people_reembed_queue).

  python -m tools.people_import.reembed_stripped            # оценка: число строк и токенов Voyage, НИЧЕГО не пишет
  python -m tools.people_import.reembed_stripped --apply    # пересчёт (voyage-4-lite), по готовности строка уходит из очереди

До миграции (очереди ещё нет) оценка считается «вживую» по тем же правилам. Только embeddings
затронутых строк, текст фактов не меняется. Сумма токенов — оценка по символам (кириллица ≈ 2–3.5 симв./токен)."""
from __future__ import annotations

import argparse
import sys
import time

import sqlalchemy as sa

from core.db import get_engine
from core.memory_rag import _embed_text, index_memories_batch
from core.people import strip_name_prefix

QUEUE = "people_reembed_queue"


def _queue_exists(c) -> bool:
    return bool(c.execute(sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": QUEUE}).scalar())


def plan() -> tuple[list[dict], str]:
    """Строки к пересчёту: [{id, embed_text}], режим ('queue' | 'live')."""
    with get_engine().connect() as c:
        if _queue_exists(c):
            rows = c.execute(sa.text(
                "SELECT m.id, m.fact_text, m.related_to, m.category FROM memories m "
                f"JOIN {QUEUE} q ON q.memory_id = m.id WHERE m.fact_text <> '' ORDER BY m.id")).all()
            return [{"id": r.id, "embed_text": _embed_text(r.fact_text, r.related_to, r.category)} for r in rows], "queue"
        rows = c.execute(sa.text(
            "SELECT m.id, m.fact_text, m.related_to, m.category, p.name, p.aliases FROM memories m "
            "JOIN people p ON p.id = m.person_id WHERE m.fact_text <> '' AND NOT p.is_self "
            "AND m.embedding IS NOT NULL ORDER BY m.id")).all()
    out = []
    for r in rows:
        new = strip_name_prefix(r.fact_text, r.name, r.aliases or [])
        if new != r.fact_text and new.strip():
            out.append({"id": r.id, "embed_text": _embed_text(new, r.related_to, r.category)})
    return out, "live"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--sleep", type=float, default=21.0, help="пауза между батчами (free-tier 3 RPM)")
    a = ap.parse_args(argv)
    items, mode = plan()
    chars = sum(len(i["embed_text"]) for i in items)
    print("режим: %s · строк к пересчёту: %d · символов: %d · токенов Voyage ≈ %d–%d (voyage-4-lite)"
          % (mode, len(items), chars, chars // 3.5, chars // 2))
    if not a.apply:
        print("оценка. Для пересчёта: --apply (после миграции).")
        return 0
    if mode != "queue":
        print("очереди нет — сначала миграция (alembic upgrade head).", file=sys.stderr)
        return 2
    done = 0
    for i in range(0, len(items), a.batch):
        if i:
            time.sleep(a.sleep)
        chunk = items[i:i + a.batch]
        n = index_memories_batch([{"memory_id": str(x["id"]), "embed_text": x["embed_text"]} for x in chunk])
        if n == len(chunk):
            with get_engine().begin() as c:
                c.execute(sa.text(f"DELETE FROM {QUEUE} WHERE memory_id = ANY(:ids)"), {"ids": [x["id"] for x in chunk]})
        done += n
        print("  эмбеддинги: %d/%d" % (done, len(items)), flush=True)
    print("готово: %d из %d (оставшиеся — в очереди, можно повторить)" % (done, len(items)))
    return 0 if done == len(items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
