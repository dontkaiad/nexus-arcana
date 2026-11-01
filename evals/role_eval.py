"""evals/role_eval.py — A/B двух моделей на коротких Haiku-ролях (parse / route / spell): те же функции, что в боте.

    python -m evals.role_eval run --a claude-haiku-4-5-20251001 --b claude-haiku-5-5 [--cases файл.json]

Для каждой функции и входа: ответ модели A vs B, % совпадений по нормализованным полям, латентность p50/p95.
Без эталона: показывает, ИЗМЕНИЛСЯ ли ответ при смене модели. Расхождения — в stdout (синтетика не секретна) или
в --out (data/evals/, если кейсы свои).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any, Callable, Dict, List

from evals.common import SYNTHETIC, dump_jsonl, pct, percentile

_VOLATILE = {"confidence", "clarification_question", "reasoning"}


def _norm(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items() if k not in _VOLATILE}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, str):
        return " ".join(v.lower().split())
    return v


async def _timed(fn: Callable, *a, **kw):
    t0 = time.monotonic()
    try:
        out = await fn(*a, **kw)
    except Exception as e:                       # сбой функции — тоже результат сравнения
        out = {"error": type(e).__name__}
    return out, int((time.monotonic() - t0) * 1000)


def _functions():
    """Функции, которые РЕАЛЬНО вызывает бот (не мёртвый код): роль → вызов."""
    from arcana.handlers.base import ROUTER_SYSTEM
    from core import classifier, deleter, memory, preprocess
    from core.claude_client import ask_claude

    async def route_intent(t):          # ровно тот вызов, что в arcana.handlers.base.route_message
        return (await ask_claude(t, system=ROUTER_SYSTEM, max_tokens=10, role="route", temperature=0)).strip().lower()

    return {
        "route_intent": ("route", route_intent),
        "parse_delete": ("parse", lambda t: deleter.parse_delete_intent(t)),
        "parse_fact": ("parse", lambda t: memory._parse_fact(t)),
        "task_category": ("parse", lambda t: classifier._haiku_task_category(t)),
        "spell": ("spell", lambda t: preprocess.normalize_text(t, user_id="")),
    }


async def run(cases: Dict[str, List[str]], a: str, b: str) -> Dict[str, Any]:
    fns = _functions()
    result: Dict[str, Any] = {}
    diffs: List[Dict[str, Any]] = []
    for name, (role, fn) in fns.items():
        texts = cases.get(name, [])
        same, la, lb = 0, [], []
        for t in texts:
            os.environ[f"MODEL_{role.upper()}"] = a
            ra, ma = await _timed(fn, t)
            os.environ[f"MODEL_{role.upper()}"] = b
            rb, mb = await _timed(fn, t)
            la.append(ma); lb.append(mb)
            if _norm(ra) == _norm(rb):
                same += 1
            else:
                diffs.append({"fn": name, "input": t, "a": ra, "b": rb})
        os.environ.pop(f"MODEL_{role.upper()}", None)
        result[name] = {"n": len(texts), "agree_pct": pct(same, len(texts)),
                        "latency_ms_a_p50": percentile(la, 0.5), "latency_ms_b_p50": percentile(lb, 0.5)}
    total = sum(r["n"] for r in result.values())
    agree = sum(round(r["agree_pct"] * r["n"] / 100) for r in result.values())
    result["_overall"] = {"n": total, "agree_pct": pct(agree, total), "model_a": a, "model_b": b}
    result["_diffs"] = diffs
    return result


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.role_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--a", default="claude-haiku-4-5-20251001"); r.add_argument("--b", default="claude-haiku-5-5")
    r.add_argument("--cases", default=str(SYNTHETIC / "role_cases.json"))
    r.add_argument("--out", default="")
    a = ap.parse_args()
    cases = json.loads(open(a.cases, encoding="utf-8").read())
    res = asyncio.run(run(cases, a.a, a.b))
    diffs = res.pop("_diffs")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if a.out:
        dump_jsonl(diffs, a.out)
    else:
        for d in diffs:
            print(json.dumps(d, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
