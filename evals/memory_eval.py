"""evals/memory_eval.py — качество поиска по памяти: recall@5 по вопросам с ожидаемыми id + доля верно выбранных маршрутов.

Метрики (на вопрос, затем среднее):
  recall@5     = |ожидаемые ∩ первые 5 выданных| / min(5, |ожидаемые|)  (если ожидаемых нет — вопрос «правильно ничего»:
                 1.0, когда выдача пуста);
  precision@5  = |ожидаемые ∩ первые 5| / |первые 5|;
  route_acc    = маршрут (direct / birthday / hybrid_self / hybrid …) совпал с ожидаемым (если задан).
Режимы:
  offline   — синтетика (evals/synthetic/memory_world.py), Postgres как в CI, вектор выключен (только FTS и разбор вопроса);
  run       — приватные кейсы data/evals/memory_cases.jsonl {"query","expected_ids":[…],"route"?}: на проде, реальные id;
  suggest   — для вопроса печатает кандидатов (id / тип / категория / группа), без текстов — помочь разметить expected_ids;
  snapshot  — сохранить ТЕКУЩУЮ выдачу как эталон (unreviewed: проверить глазами перед использованием как «правда»).
"""
from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Dict, List, Optional

from evals.common import PRIVATE, dump_jsonl, load_jsonl, pct, report


def _flat_ids(res) -> List[int]:
    return [int(h.memory.id) for g in res.groups for h in g.hits]


def score(expected: List[int], got_ids: List[int], k: int = 5) -> Dict[str, float]:
    top = got_ids[:k]
    exp = set(expected)
    hit = len(exp & set(top))
    if not exp:
        recall = 1.0 if not top else 0.0
        prec = 1.0 if not top else 0.0
    else:
        recall = hit / min(k, len(exp))
        prec = hit / len(top) if top else 0.0
    return {"recall": min(1.0, recall), "precision": prec}


async def _run(cases: List[Dict[str, Any]], user_id: str, rerank: bool, k: int = 5) -> Dict[str, Any]:
    from core import memory_search
    rec, prec, route_ok, route_n, bad = [], [], 0, 0, []
    routes: Dict[str, int] = {}
    for c in cases:
        res = await memory_search.answer(c["query"], user_id=user_id, k=k, do_rerank=rerank)
        s = score(c["expected_ids"], _flat_ids(res), k)
        rec.append(s["recall"]); prec.append(s["precision"])
        routes[res.route] = routes.get(res.route, 0) + 1
        if c.get("route"):
            route_n += 1
            route_ok += int(res.route == c["route"])
        if s["recall"] < 1.0 or (c.get("route") and res.route != c["route"]):
            bad.append({"query_idx": cases.index(c), "route": res.route, "recall": round(s["recall"], 2),
                        "expected": len(c["expected_ids"]), "got": len(_flat_ids(res))})
    n = len(cases)
    return {"questions": n, "recall_at_5_pct": pct(sum(rec), n), "precision_at_5_pct": pct(sum(prec), n),
            "route_acc_pct": pct(route_ok, route_n) if route_n else None, "routes": routes, "misses": bad}


def run_offline() -> Dict[str, Any]:
    """Синтетический мир → recall@5 без вектора и без LLM (детерминированно)."""
    from evals.synthetic.memory_world import CASES, SYN_USER, world
    os.environ.setdefault("MEMORY_QUERY_UNDERSTANDING", "1")
    from unittest.mock import patch
    from core import memory_search as ms
    with world() as ids:
        cases = [{"query": q, "expected_ids": [ids[k] for k in exp], "route": route} for q, exp, route in CASES]
        # вектор выключаем (нет ключа Voyage в CI): оцениваем разбор вопроса + FTS + прямой путь
        with patch.object(ms, "_embed", return_value=[]):
            return asyncio.run(_run(cases, SYN_USER, rerank=False))


def _owner(sa, get_engine) -> str:
    with get_engine().connect() as c:
        row = c.execute(sa.text("SELECT user_id FROM memories WHERE is_current AND NOT is_archived "
                                "GROUP BY user_id ORDER BY count(*) DESC LIMIT 1")).first()
    return row[0] if row else ""


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.memory_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("offline")
    r = sub.add_parser("run"); r.add_argument("--cases", default=str(PRIVATE / "memory_cases.jsonl"))
    r.add_argument("--user-id", default=""); r.add_argument("--rerank", action="store_true")
    s = sub.add_parser("suggest"); s.add_argument("query", nargs="+"); s.add_argument("--user-id", default="")
    sn = sub.add_parser("snapshot"); sn.add_argument("--queries", required=True, help="файл со строками-вопросами или JSONL")
    sn.add_argument("--out", default=str(PRIVATE / "memory_cases.jsonl")); sn.add_argument("--user-id", default="")
    a = ap.parse_args()
    if a.cmd == "offline":
        report("memory retrieval (offline, синтетика, без вектора)", run_offline())
        return 0
    import sqlalchemy as sa
    from core.db import get_engine
    uid = getattr(a, "user_id", "") or _owner(sa, get_engine)
    if a.cmd == "run":
        report("memory retrieval (private cases)", asyncio.run(_run(load_jsonl(a.cases), uid, a.rerank)))
    elif a.cmd == "suggest":
        from core import memory_search
        from core.repos.pg_people_repo import fact_type
        for q in a.query:
            res = asyncio.run(memory_search.answer(q, user_id=uid, k=20, do_rerank=False))
            print(f"\n{q!r}: route={res.route} kind={res.kind} subject={res.subject}")
            for g in res.groups:
                for h in g.hits:
                    m = h.memory
                    print(f"  id={m.id} type={memory_search.effective_type(m.fact, fact_type(m.key))} cat={m.category} grp={g.person_id or '-'}")
    else:
        from core import memory_search
        qs = [x["query"] if isinstance(x, dict) else x for x in
              ([__import__("json").loads(l) if l.strip().startswith("{") else l.strip() for l in open(a.queries, encoding="utf-8") if l.strip()])]
        rows = []
        for q in qs:
            res = asyncio.run(memory_search.answer(q, user_id=uid, k=20, do_rerank=False))
            rows.append({"query": q, "expected_ids": _flat_ids(res), "route": res.route, "status": "unreviewed-snapshot"})
        print(f"записано: {dump_jsonl(rows, a.out)} → {a.out} (эталон НЕ проверен: просмотри ids и поправь)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
