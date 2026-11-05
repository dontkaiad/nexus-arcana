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


# ── SQL-часть: предложения, объединение, «Это разное», разъединение ──────────────────────────────────────────────────
# Объединённый дубль скрыт через is_archived + merged_into → все существующие выборки (поиск, бот, карточки) его не видят.
# Ничего не удаляется: цитаты и счётчики остаются в строке-дубле, у оставшегося факта они показываются на чтении (merged_info).

def similar_groups(person_id: int, user_id: str, threshold: float = DEFAULT_THRESHOLD, limit: int = 200) -> List[Dict]:
    """Группы похожих фактов карточки (cosine по готовым эмбеддингам, без LLM/Voyage). Отклонённые, архивные и без вектора не берутся."""
    from sqlalchemy import text
    from core.db import get_engine
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, fact_text, source, review_status, mention_count, category FROM memories "
            "WHERE person_id = :p AND user_id = :u AND NOT is_archived AND fact_text <> '' AND embedding IS NOT NULL "
            "AND review_status <> 'rejected'"), {"p": person_id, "u": user_id}).all()
        if len(rows) < 2:
            return []
        edges = c.execute(text(
            "SELECT a.id AS x, b.id AS y, 1 - (a.embedding <=> b.embedding) AS cos FROM memories a JOIN memories b "
            "ON a.person_id = b.person_id AND a.id < b.id WHERE a.person_id = :p AND a.user_id = :u "
            "AND NOT a.is_archived AND NOT b.is_archived AND a.fact_text <> '' AND b.fact_text <> '' "
            "AND a.review_status <> 'rejected' AND b.review_status <> 'rejected' "
            "AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL AND 1 - (a.embedding <=> b.embedding) >= :t"),
            {"p": person_id, "u": user_id, "t": threshold}).all()
        dismissed = {(r.a, r.b) for r in c.execute(text(
            "SELECT a, b FROM memory_dedup_dismissed WHERE user_id = :u"), {"u": user_id})}
    by_id = {r.id: r for r in rows}
    pol = {r.id: polarity(r.fact_text) for r in rows}
    groups = clusters([(e.x, e.y, float(e.cos)) for e in edges], pol, dismissed, threshold)[:limit]
    out = []
    for g in groups:
        items = [{"id": i, "text": by_id[i].fact_text, "source": by_id[i].source, "review_status": by_id[i].review_status,
                  "mention_count": by_id[i].mention_count or 1} for i in g]
        keep = suggest_keep(items)
        out.append({"key": "-".join(str(i) for i in g), "keep_id": keep, "items": items})
    return out


def _own(c, user_id: str, person_id: int, ids: Sequence[int]):
    from sqlalchemy import text
    return c.execute(text(
        "SELECT id, is_archived, merged_into FROM memories WHERE id = ANY(:i) AND user_id = :u AND person_id = :p"),
        {"i": list(ids), "u": user_id, "p": person_id}).all()


def merge(user_id: str, person_id: int, keep_id: int, ids: Sequence[int]) -> int:
    """Скрыть `ids` (кроме keep_id) под фактом keep_id. Возвращает, сколько объединено; ValueError — чужие/уже объединённые."""
    from sqlalchemy import text
    from core.db import get_engine
    kids = [i for i in dict.fromkeys(ids) if i != keep_id]
    if not kids:
        raise ValueError("nothing_to_merge")
    with get_engine().begin() as c:
        rows = {r.id: r for r in _own(c, user_id, person_id, [keep_id] + kids)}
        if len(rows) != len(kids) + 1:
            raise ValueError("not_found")
        if rows[keep_id].is_archived or rows[keep_id].merged_into:
            raise ValueError("keep_hidden")
        if any(rows[i].is_archived for i in kids):
            raise ValueError("already_hidden")
        c.execute(text("UPDATE memories SET is_archived = true, merged_into = :k, updated_at = now() WHERE id = ANY(:i)"),
                  {"k": keep_id, "i": kids})
        # факты, ранее объединённые в скрываемые, переходят к новому survivor-у
        c.execute(text("UPDATE memories SET merged_into = :k WHERE merged_into = ANY(:i)"), {"k": keep_id, "i": kids})
    return len(kids)


def dismiss(user_id: str, person_id: int, ids: Sequence[int]) -> int:
    """«Это разное»: все пары группы больше не предлагаются."""
    from sqlalchemy import text
    from core.db import get_engine
    ids = sorted(set(ids))
    if len(ids) < 2:
        raise ValueError("nothing_to_dismiss")
    with get_engine().begin() as c:
        if len(_own(c, user_id, person_id, ids)) != len(ids):
            raise ValueError("not_found")
        n = 0
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                n += c.execute(text(
                    "INSERT INTO memory_dedup_dismissed (user_id, a, b) VALUES (:u, :a, :b) ON CONFLICT DO NOTHING"),
                    {"u": user_id, "a": ids[x], "b": ids[y]}).rowcount
    return n


def unmerge(user_id: str, person_id: int, keep_id: int, child_id: int = None) -> int:
    """Вернуть скрытый дубль (или все дубли факта keep_id) в список; пары заодно помечаются «разное», чтобы не склеиваться снова."""
    from sqlalchemy import text
    from core.db import get_engine
    with get_engine().begin() as c:
        rows = c.execute(text(
            "SELECT id FROM memories WHERE merged_into = :k AND user_id = :u AND person_id = :p AND is_archived "
            + ("AND id = :c" if child_id else "")), {"k": keep_id, "u": user_id, "p": person_id, "c": child_id}).all()
        ids = [r.id for r in rows]
        if not ids:
            raise ValueError("not_found")
        c.execute(text("UPDATE memories SET is_archived = false, merged_into = NULL, updated_at = now() WHERE id = ANY(:i)"), {"i": ids})
        for i in ids:
            a, b = min(i, keep_id), max(i, keep_id)
            c.execute(text("INSERT INTO memory_dedup_dismissed (user_id, a, b) VALUES (:u, :a, :b) ON CONFLICT DO NOTHING"),
                      {"u": user_id, "a": a, "b": b})
    return len(ids)


def merged_info(user_id: str, keep_ids: Sequence[int]) -> Dict[int, List[Dict]]:
    """Объединённые дубли для показа под оставшимся фактом: {keep_id: [{id, text, quote, mention_count, …}]}."""
    from sqlalchemy import text
    from core.db import get_engine
    if not keep_ids:
        return {}
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT id, merged_into, fact_text, source_quote, mention_count, first_mention_date, last_mention_date, "
            "source_message_date, source_platform FROM memories WHERE merged_into = ANY(:k) AND user_id = :u ORDER BY id"),
            {"k": list(keep_ids), "u": user_id}).all()
    out: Dict[int, List[Dict]] = {}
    for r in rows:
        out.setdefault(r.merged_into, []).append({
            "id": r.id, "text": r.fact_text, "quote": r.source_quote, "mention_count": r.mention_count or 1,
            "first_mention": r.first_mention_date, "last_mention": r.last_mention_date,
            "message_date": r.source_message_date, "platform": r.source_platform})
    return out
