"""evals/grounding_eval.py — проверка grounding ответа «своими словами» (core/memory_answer.validate).

Офлайн (CI, без LLM и БД): синтетические «ответы модели» — хорошие и с выдумками — прогоняются через валидатор.
Метрики: accept_rate (хорошие приняты), reject_recall (плохие отброшены или очищены), check_match (причина = ожидаемая).
Live (на проде, реальный Haiku): `python -m evals.grounding_eval live --cases data/evals/memory_cases.jsonl` — доля
fallback / отброшенных фраз, латентность, токены на реальных вопросах (тексты не печатаются).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Dict

from core import memory_answer as ma
from evals.common import SYNTHETIC, load_jsonl, pct, percentile, report


def _groups(spec: Dict[str, Any]):
    g = ma.GGroup(n=1, label=spec.get("group_label", ""), person_id=1)
    for f in spec["facts"]:
        g.facts.append(ma.GFact(id=f["id"], fact=f["text"]))
    return [g]


def run_offline() -> Dict[str, Any]:
    spec = json.loads((SYNTHETIC / "grounding_cases.json").read_text(encoding="utf-8"))
    groups = _groups(spec)
    n = {"accept": 0, "partial": 0, "reject": 0}
    ok = {"accept": 0, "partial": 0, "reject": 0}
    check_ok = check_total = 0
    wrong = []
    for c in spec["cases"]:
        chk = ma.validate(c["answer"], groups)
        exp = c["expect"]
        n[exp] += 1
        if exp == "accept":
            good = chk.ok and not chk.dropped
        elif exp == "partial":
            good = chk.ok and bool(chk.dropped)
        else:
            good = not chk.ok
        ok[exp] += int(good)
        if not good:
            wrong.append(c["name"])
        if "check" in c:
            check_total += 1
            got = chk.check if not chk.ok else (chk.dropped[0] if chk.dropped else "")
            check_ok += int(got == c["check"])
            if got != c["check"] and c["name"] not in wrong:
                wrong.append(c["name"] + f"(check {got!r}≠{c['check']!r})")
    bad = n["reject"] + n["partial"]
    return {
        "cases": sum(n.values()),
        "accept_rate_pct": pct(ok["accept"], n["accept"]),
        "reject_recall_pct": pct(ok["reject"] + ok["partial"], bad),
        "check_match_pct": pct(check_ok, check_total),
        "wrong": wrong,
    }


async def _live(cases_path: str, user_id: str, k: int) -> Dict[str, Any]:
    from core import memory_search
    cases = load_jsonl(cases_path)
    lat, tin, tout, ok, fb, dropped, skipped = [], 0, 0, 0, 0, 0, 0
    reasons: Dict[str, int] = {}
    import os
    os.environ["MEMORY_ANSWER_LOG"] = "0"          # прогоны эвала не портят дневную статистику
    for c in cases:
        res = await memory_search.answer(c["query"], user_id=user_id, k=k, do_rerank=True)
        if not ma.eligible(res):
            skipped += 1
            continue
        o = await ma.generate(res, c["query"])
        lat.append(o.latency_ms)
        tin += o.in_tokens
        tout += o.out_tokens
        if o.ok:
            ok += 1
            dropped += int(o.reason.startswith("dropped"))
        else:
            fb += 1
            reasons[o.reason] = reasons.get(o.reason, 0) + 1
    n = ok + fb
    return {"generated": n, "skipped_without_llm": skipped, "ok": ok, "fallback": fb, "fallback_pct": pct(fb, n),
            "with_dropped_lines": dropped, "reasons": reasons, "latency_p50_ms": percentile(lat, 0.5),
            "latency_max_ms": max(lat or [0]), "tokens_in": tin, "tokens_out": tout}


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.grounding_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("offline")
    lv = sub.add_parser("live")
    lv.add_argument("--cases", required=True)
    lv.add_argument("--user-id", default="")
    lv.add_argument("--k", type=int, default=5)
    a = ap.parse_args()
    if a.cmd == "offline":
        report("grounding (offline, синтетика)", run_offline())
        return 0
    import sqlalchemy as sa
    from core.db import get_engine
    uid = a.user_id
    if not uid:
        with get_engine().connect() as c:
            row = c.execute(sa.text("SELECT user_id FROM memories WHERE is_current AND NOT is_archived "
                                    "GROUP BY user_id ORDER BY count(*) DESC LIMIT 1")).first()
        uid = row[0] if row else ""
    report("grounding (live)", asyncio.run(_live(a.cases, uid, a.k)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
