"""core/self_dedup.py — дубли среди фактов «Я»: кластеры похожих по уже посчитанным эмбеддингам (без LLM и без Voyage).

Объединяются только факты ОДНОЙ полярности («любит» не сливается с «не любит»). Кластеры — компоненты связности по
парам с cosine ≥ порога; пары, помеченные «Это разное», в рёбра не попадают. Чистые функции — SQL (pgvector) в вызывающем коде.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set, Tuple

DEFAULT_THRESHOLD = 0.82

# Отрицание / неприязнь: такие факты не сливаются с утвердительными.
_NEG = re.compile(r"\bне\b|\bни\b|ненавид|терпеть не|бесит|раздража|нельзя|избега|боится|\bбез\b|никогда", re.I)


def polarity(text: str) -> str:
    return "neg" if _NEG.search(text or "") else "pos"


def clusters(edges: Iterable[Tuple[int, int, float]], pol: Dict[int, str],
             dismissed: Set[Tuple[int, int]] = frozenset(), threshold: float = DEFAULT_THRESHOLD) -> List[List[int]]:
    """Компоненты связности. edges: (id_a, id_b, cosine). Пара пропускается, если cosine ниже порога, полярности разные
    или пара в dismissed ((min,max)). Возвращает кластеры из ≥2 id, внутри — по возрастанию, кластеры — по убыванию размера."""
    parent: Dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, cos in edges:
        if cos < threshold or pol.get(a) != pol.get(b):
            continue
        if (min(a, b), max(a, b)) in dismissed:
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: Dict[int, List[int]] = {}
    for x in list(parent):
        groups.setdefault(find(x), []).append(x)
    out = [sorted(g) for g in groups.values() if len(g) >= 2]
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
