"""core/self_dedup.py — дубли среди фактов «Я»: кластеры похожих по уже посчитанным эмбеддингам (без LLM и без Voyage).

Объединяются только факты ОДНОЙ полярности («любит» не сливается с «не любит»). Кластеры — группы с полной связью (каждый
с каждым cosine ≥ порога); пары, помеченные «Это разное», в рёбра не попадают. Чистые функции — SQL (pgvector) в вызывающем коде.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set, Tuple

DEFAULT_THRESHOLD = 0.90

# Отрицание / неприязнь: такие факты не сливаются с утвердительными.
_NEG = re.compile(r"\bне\b|\bни\b|ненавид|терпеть не|бесит|раздража|нельзя|избега|боится|\bбез\b|никогда", re.I)


def polarity(text: str) -> str:
    return "neg" if _NEG.search(text or "") else "pos"


def clusters(edges: Iterable[Tuple[int, int, float]], pol: Dict[int, str],
             dismissed: Set[Tuple[int, int]] = frozenset(), threshold: float = DEFAULT_THRESHOLD) -> List[List[int]]:
    """Жадная кластеризация с ПОЛНОЙ связью: факт входит в группу, только если похож (cosine ≥ порога, та же полярность,
    пара не в dismissed) на КАЖДОГО её члена. Компоненты связности для коротких фактов не годятся: цепочка 0.83–0.83–0.83
    сливает полтысячи разных фактов в одну «группу». edges: (id_a, id_b, cosine). Возвращает группы из ≥2 id (внутри — по
    возрастанию; группы — по убыванию размера)."""
    adj: Dict[int, Dict[int, float]] = {}
    for a, b, cos in edges:
        if cos < threshold or pol.get(a) != pol.get(b) or (min(a, b), max(a, b)) in dismissed:
            continue
        adj.setdefault(a, {})[b] = cos
        adj.setdefault(b, {})[a] = cos
    taken: Set[int] = set()
    out: List[List[int]] = []
    # сначала самые «связные» факты: они задают ядро группы
    for seed in sorted(adj, key=lambda x: (-len(adj[x]), x)):
        if seed in taken:
            continue
        group = [seed]
        for cand in sorted(adj[seed], key=lambda x: (-adj[seed][x], x)):
            if cand in taken:
                continue
            if all(cand in adj and m in adj[cand] for m in group):
                group.append(cand)
        if len(group) >= 2:
            taken.update(group)
            out.append(sorted(group))
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


def suggest_keep(rows: Sequence[Dict]) -> int:
    """Какая формулировка остаётся по умолчанию: ручной факт (source=manual) приоритетнее; дальше принятый, больше упоминаний,
    более ранний id. rows: {id, source, review_status, mention_count}."""
    def key(r: Dict):
        return (0 if r.get("source") == "manual" else 1,
                0 if r.get("review_status") == "confirmed" else 1,
                -(r.get("mention_count") or 1),
                r["id"])
    return min(rows, key=key)["id"]
