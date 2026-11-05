"""scripts/self_dedup_dryrun.py — dry-run дедупа фактов «Я» (READ-ONLY, без LLM и Voyage: берёт готовые эмбеддинги из БД).

    docker exec -i nexus-bot python - [--threshold 0.82] [--out /tmp/dedup.md] < scripts/self_dedup_dryrun.py

Печатает ТОЛЬКО числа (кластеры/факты по порогам) и пишет файл с текстами кластеров (хранить локально, data/evals/).
Учитывает то же, что и разнос по цитате: берёт факты, которые остаются в «Я» (attribution = keep) и не отклоняются как
временные, плюс уже принятые (confirmed). Полярность: «любит» и «не любит» не смешиваются.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from collections import Counter

THRESHOLDS = (0.84, 0.86, 0.88, 0.90, 0.92, 0.94)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--out", default="/tmp/dedup.md")
    a = ap.parse_args()
    import sqlalchemy as sa
    from core.db import get_engine
    from core.memory_subjects import attribution, temp_signal
    from core.self_dedup import clusters, polarity, suggest_keep

    eng = get_engine()
    with eng.connect() as c:
        me = c.execute(sa.text("SELECT id FROM people WHERE is_self")).scalar_one()
        rows = c.execute(sa.text(
            "SELECT id, fact_text, source, review_status, mention_count, source_quote, quote_author, embedding IS NOT NULL AS emb "
            "FROM memories WHERE person_id = :p AND NOT is_archived AND review_status <> 'rejected' AND fact_text <> ''"),
            {"p": me}).all()
        keep_ids = set()
        for r in rows:
            if r.review_status == "confirmed" or (attribution(r.fact_text, r.source_quote, r.quote_author)[0] == "keep"
                                                  and not temp_signal(r.fact_text)):
                keep_ids.add(r.id)
        by_id = {r.id: r for r in rows if r.id in keep_ids}
        edges = [(r.a, r.b, float(r.cos)) for r in c.execute(sa.text(
            "SELECT a.id AS a, b.id AS b, 1 - (a.embedding <=> b.embedding) AS cos FROM memories a JOIN memories b ON a.id < b.id "
            "WHERE a.person_id = :p AND b.person_id = :p AND NOT a.is_archived AND NOT b.is_archived "
            "AND a.review_status <> 'rejected' AND b.review_status <> 'rejected' AND a.fact_text <> '' AND b.fact_text <> '' "
            "AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL AND 1 - (a.embedding <=> b.embedding) >= :t"),
            {"p": me, "t": min(THRESHOLDS)}).all()]
    edges = [e for e in edges if e[0] in by_id and e[1] in by_id]
    pol = {i: polarity(r.fact_text) for i, r in by_id.items()}
    cross = sum(1 for x, y, cs in edges if cs >= 0.90 and pol[x] != pol[y])
    print(f"фактов в расчёте: {len(by_id)} (без эмбеддинга: {sum(1 for r in by_id.values() if not r.emb)}) | "
          f"полярность: {dict(Counter(pol.values()))} | пар ≥0.90 разной полярности (отброшены): {cross}")
    print(f"{'порог':>6} {'кластеров':>10} {'фактов в них':>13} {'макс. размер':>13} {'размеры (топ-8)':>20}")
    for t in THRESHOLDS:
        cl = clusters(edges, pol, threshold=t)
        print(f"{t:6.2f} {len(cl):10d} {sum(map(len, cl)):13d} {max(map(len, cl), default=0):13d}   {[len(g) for g in cl[:8]]}")

    cl = clusters(edges, pol, threshold=a.threshold)
    cos = {(min(x, y), max(x, y)): cs for x, y, cs in edges}
    rnd = random.Random(3)
    show = cl[:25] + rnd.sample(cl[25:], min(35, max(0, len(cl) - 25)))          # самые большие + случайные
    with open(a.out, "w") as f:
        f.write(f"# Дедуп «Я»: кластеры при cosine ≥ {a.threshold} (ЛОКАЛЬНО, не в git)\n\n")
        f.write(f"Всего кластеров: {len(cl)}, фактов в них: {sum(map(len, cl))}; показано {len(show)} "
                "(25 крупнейших + случайные). `★` — формулировка по умолчанию (ручной факт приоритетнее).\n\n")
        for n, g in enumerate(show, 1):
            rs = [{"id": i, "source": by_id[i].source, "review_status": by_id[i].review_status,
                   "mention_count": by_id[i].mention_count} for i in g]
            star = suggest_keep(rs)
            sims = [cos[(min(x, y), max(x, y))] for x in g for y in g if x < y and (min(x, y), max(x, y)) in cos]
            f.write(f"### Группа {n}: {len(g)} фактов, cosine {min(sims):.2f}–{max(sims):.2f}, полярность {pol[g[0]]}\n")
            for i in g:
                r = by_id[i]
                f.write(f"- {'★' if i == star else ' '} #{i} [{r.source}/{r.review_status}/×{r.mention_count or 1}] {r.fact_text}\n")
            f.write("\n")
    print("файл:", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
