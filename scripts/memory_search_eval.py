"""scripts/memory_search_eval.py — срез top-5 поиска по 🧠 Память «до/после» (READ-ONLY).

Печатает ТОЛЬКО id / категорию / score / источник — без текстов фактов (там личные данные).
Ничего не пишет в БД. Самодостаточен: для режима `old` не импортирует новый модуль, поэтому
его можно запустить внутри контейнера со СТАРЫМ кодом, не накатывая ветку.

    # до (код main в контейнере бота):
    docker exec -i <bot-container> python - --mode old < scripts/memory_search_eval.py
    # после (код ветки):
    docker exec -i <bot-container> python - --mode new < scripts/memory_search_eval.py

--user-id не задан → берётся владелец с наибольшим числом фактов (сам id не печатается).
--mode old : прежний путь search_memory (ILIKE-OR top-10, при <3 хитов — Voyage+Haiku-фоллбэк).
--mode new : core.memory_search.hybrid_search(k=5, Haiku-реранк), плюс RRF-срез без реранка.
Колонки: rank | id | category | score | src
  old: score = cosine (только у semantic-довеска), src = ilike|semantic
  new: score = RRF-сумма, src = fts#N,vec#M (позиции в ногах); '+rerank' — прошёл Haiku
"""
from __future__ import annotations

import argparse
import asyncio
import sys

QUERIES = ["что я не ем", "когда у Оли др", "что не любит Миша", "номер квартиры"]


def _auto_user(sa, eng):
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT user_id FROM memories WHERE is_current AND NOT is_archived "
            "GROUP BY user_id ORDER BY count(*) DESC LIMIT 1")).first()
    return row[0] if row else ""


def _cat_by_id(sa, eng, ids):
    if not ids:
        return {}
    with eng.connect() as c:
        rows = c.execute(sa.text("SELECT id, category FROM memories WHERE id = ANY(:i)"),
                         {"i": [int(i) for i in ids]}).all()
    return {str(r[0]): (r[1] or "—") for r in rows}


async def run_old(q, user_id):
    from core import memory as mem
    tokens = mem._tokenize_hint(q) or [q]
    ilike = await mem._mem_repo.search(tokens, user_id=user_id, page_size=10)
    merged = await mem._semantic_search_memory(q, ilike, user_id=user_id)
    ilike_ids = {m.id for m in ilike}
    return [(m.id, None, "ilike" if m.id in ilike_ids else "semantic") for m in merged[:5]]


async def run_new(q, user_id, rerank):
    from core import memory_search
    hits = await memory_search.hybrid_search(q, user_id=user_id, k=5, do_rerank=rerank)
    return [(h.memory.id, h.score, f"fts#{h.fts_rank or '-'},vec#{h.vec_rank or '-'}"
             + ("+rerank" if h.reranked else "")) for h in hits]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["old", "new"], required=True)
    ap.add_argument("--user-id", default="")
    ap.add_argument("--query", action="append", help="свой запрос (можно несколько); по умолчанию 4 стандартных")
    args = ap.parse_args()

    import sqlalchemy as sa
    from core.db import get_engine
    eng = get_engine()
    user_id = args.user_id or _auto_user(sa, eng)
    queries = args.query or QUERIES

    for q in queries:
        print(f"\n=== [{args.mode}] {q!r} ===")
        if args.mode == "old":
            rows = await run_old(q, user_id)
        else:
            rows = await run_new(q, user_id, rerank=True)
        cats = _cat_by_id(sa, eng, [r[0] for r in rows])
        if not rows:
            print("  (пусто)")
        for i, (mid, score, src) in enumerate(rows, 1):
            sc = "-" if score is None else f"{score:.4f}"
            print(f"  {i}. id={mid} cat={cats.get(str(mid), '—')} score={sc} src={src}")
        if args.mode == "new":
            nr = await run_new(q, user_id, rerank=False)
            print("  без реранка (RRF top-5): " + ", ".join(f"{m}" for m, _, _ in nr))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
