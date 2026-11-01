"""evals/classifier_eval.py — классификатор сообщений: A/B-реплей двух моделей и офлайн-проверка детерминированных ветвей.

  collect  — из логов бота (stdin) или из файла сбора (--captured, EVAL_CAPTURE=1) выбрать N последних РАЗНЫХ входящих сообщений → JSONL (приватно: data/evals/).
             docker logs --since 30d nexus-bot 2>&1 | python -m evals.classifier_eval collect --limit 100
  run      — прогнать каждое сообщение через classify() моделью A и моделью B (роль classify, MODEL_CLASSIFY в окружении):
             % совпадений по типам и «строго» (весь JSON), долю сообщений, дошедших до LLM, латентность p50/p95, токены, $.
             Список расхождений — в --out (data/evals/…, приватно); в консоль — только числа.
  offline  — синтетика evals/synthetic/classifier_cases.jsonl: детерминированные ветви (regex-префильтры) дают ожидаемый тип
             БЕЗ вызова LLM — регрессия для CI.
Ground truth нет: A/B показывает «изменился ли ответ при смене модели», а не «кто прав»; расхождения смотрит человек.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from evals.common import PRIVATE, SYNTHETIC, dump_jsonl, load_jsonl, pct, percentile, report

_LOG_RE = re.compile(r"classify: input text=(?P<q>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")


def collect_captured(path: str, limit: int) -> List[Dict[str, Any]]:
    """Сообщения из data/evals/captured_messages.jsonl (включается EVAL_CAPTURE=1): последние N разных."""
    seen, out = set(), []
    for r in load_jsonl(path):
        t = (r.get("text") or "").strip()
        if len(t) < 3 or t in seen:
            continue
        seen.add(t)
        out.append({"text": t, "note": "captured"})
    return out[-limit:]


def collect(stream, limit: int) -> List[Dict[str, Any]]:
    import ast
    seen, out = set(), []
    for line in stream:
        m = _LOG_RE.search(line)
        if not m:
            continue
        try:
            t = ast.literal_eval(m.group("q")).strip()
        except Exception:
            continue
        if len(t) < 3 or t in seen:
            continue
        seen.add(t)
        out.append({"text": t, "note": "из лога (обрезано логом до 100 символов)"})
    return out[-limit:]


async def classify_once(text: str, model: str, user_id: str = "") -> Dict[str, Any]:
    """classify() выбранной моделью. Возвращает items, дошло ли до LLM, латентность, токены."""
    from core import claude_client, classifier
    os.environ["MODEL_CLASSIFY"] = model
    stat = {"llm": False, "ms": 0, "in": 0, "cw": 0, "cr": 0, "out": 0}
    real = claude_client.ask_claude          # оригинал, а не текущая привязка в classifier (там может стоять чужая обёртка)

    async def spy(*a, **kw):
        import time
        usage: Dict[str, int] = {}
        t0 = time.monotonic()
        raw = await real(*a, usage_out=usage, **kw)
        stat.update(llm=True, ms=int((time.monotonic() - t0) * 1000), **{
            "in": usage.get("input_tokens", 0), "cw": usage.get("cache_creation_input_tokens", 0),
            "cr": usage.get("cache_read_input_tokens", 0), "out": usage.get("output_tokens", 0)})
        return raw

    with patch.object(classifier, "ask_claude", spy):
        items = await classifier.classify(text, tz_offset=3, user_id=user_id)
    return {"items": items, "stat": stat}


def _types(items) -> List[str]:
    return [i.get("type", "?") for i in items]


async def _ab(cases, a: str, b: str, user_id: str):
    """Строго последовательно: модель выбирается переменной окружения MODEL_CLASSIFY, параллельные задачи гонялись бы."""
    out = []
    for c in cases:
        ra = await classify_once(c["text"], a, user_id)
        rb = await classify_once(c["text"], b, user_id)
        out.append((c, ra, rb))
    return out


def run_ab(cases, a: str, b: str, user_id: str, out: Optional[str]) -> Dict[str, Any]:
    from scripts.llm_usage_stats import cost
    rows = asyncio.run(_ab(cases, a, b, user_id))
    llm = [(c, ra, rb) for c, ra, rb in rows if ra["stat"]["llm"] or rb["stat"]["llm"]]
    same_types = [x for x in llm if _types(x[1]["items"]) == _types(x[2]["items"])]
    same_strict = [x for x in llm if json.dumps(x[1]["items"], sort_keys=True) == json.dumps(x[2]["items"], sort_keys=True)]
    diffs = [{"idx": i, "text": c["text"], "a_types": _types(ra["items"]), "b_types": _types(rb["items"]),
              "a_items": ra["items"], "b_items": rb["items"]}
             for i, (c, ra, rb) in enumerate(rows) if _types(ra["items"]) != _types(rb["items"])
             or json.dumps(ra["items"], sort_keys=True) != json.dumps(rb["items"], sort_keys=True)]
    lat = lambda k: [x[k]["stat"]["ms"] for x in llm if x[k]["stat"]["llm"]]
    tok = lambda k, f: sum(x[k]["stat"][f] for x in llm)
    summary = {
        "model_a": a, "model_b": b, "messages": len(rows), "reached_llm": len(llm),
        "agree_types_pct": pct(len(same_types), len(llm)), "agree_strict_pct": pct(len(same_strict), len(llm)),
        "disagreements": len(diffs),
        "latency_ms_a": {"p50": percentile(lat(1), 0.5), "p95": percentile(lat(1), 0.95)},
        "latency_ms_b": {"p50": percentile(lat(2), 0.5), "p95": percentile(lat(2), 0.95)},
        "tokens_a": {f: tok(1, f) for f in ("in", "cw", "cr", "out")},
        "tokens_b": {f: tok(2, f) for f in ("in", "cw", "cr", "out")},
    }
    summary["usd_a_per_1k_msgs"] = round(1000 * cost(a, summary["tokens_a"]["in"], summary["tokens_a"]["cw"], summary["tokens_a"]["cr"], summary["tokens_a"]["out"]) / max(1, len(llm)), 4)
    summary["usd_b_per_1k_msgs"] = round(1000 * cost(b, summary["tokens_b"]["in"], summary["tokens_b"]["cw"], summary["tokens_b"]["cr"], summary["tokens_b"]["out"]) / max(1, len(llm)), 4)
    if out:
        summary["diff_file"] = out if out == "-" else f"{out} ({dump_jsonl(diffs, out)} строк)"
    return summary


def run_offline() -> Dict[str, Any]:
    """Синтетика: детерминированные ветви должны вернуть ожидаемый тип, не дёргая LLM."""
    from core import classifier
    cases = load_jsonl(str(SYNTHETIC / "classifier_cases.jsonl"))
    called = AsyncMock(return_value='[{"type":"unknown"}]')
    ok, wrong = 0, []
    with patch.object(classifier, "ask_claude", called), \
         patch.object(classifier, "_known_budget_positions", AsyncMock(return_value=([], []))):
        for c in cases:
            items = asyncio.run(classifier.classify(c["text"], tz_offset=3, user_id=""))
            if _types(items) == c["expected_types"]:
                ok += 1
            else:
                wrong.append({"text": c["text"], "got": _types(items), "expected": c["expected_types"]})
    return {"cases": len(cases), "accuracy_pct": pct(ok, len(cases)), "llm_calls": called.await_count, "wrong": wrong}


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.classifier_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect"); c.add_argument("--limit", type=int, default=100)
    c.add_argument("--captured", default="", help="вместо логов (stdin) — файл data/evals/captured_messages.jsonl")
    r = sub.add_parser("run")
    r.add_argument("--cases", default=str(PRIVATE / "classifier_cases.jsonl"))
    r.add_argument("--a", default="claude-haiku-4-5-20251001"); r.add_argument("--b", default="claude-haiku-5-5")
    r.add_argument("--out", default="-", help="куда писать расхождения: путь в data/evals/ или '-' (stdout)")
    r.add_argument("--user-id", default="")
    sub.add_parser("offline")
    a = ap.parse_args()
    if a.cmd == "collect":
        import sys
        rows = collect_captured(a.captured, a.limit) if a.captured else collect(sys.stdin, a.limit)
        n = dump_jsonl(rows, "-")
        print(f"собрано {n}", file=sys.stderr)
    elif a.cmd == "run":
        import sys
        summary = run_ab(load_jsonl(a.cases), a.a, a.b, a.user_id, a.out)
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr if a.out == "-" else sys.stdout)
    else:
        report("classifier (offline, синтетика)", run_offline())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
