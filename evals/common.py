"""Общие хелперы эвалов: пути, JSONL, агрегаты. Приватные выходы пишем только в data/evals/ (gitignore)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "evals" / "synthetic"
PRIVATE = ROOT / "data" / "evals"


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    """'-' — stdin (удобно: ssh … < data/evals/cases.jsonl)."""
    f = sys.stdin if path == "-" else open(path, encoding="utf-8")
    try:
        return [json.loads(line) for line in f if line.strip()]
    finally:
        if f is not sys.stdin:
            f.close()


def dump_jsonl(rows: Iterable[Dict[str, Any]], path: str) -> int:
    """Пишет JSONL. Путь вне data/evals/ допускаем только для '-' (stdout): приватные результаты не должны попасть в git."""
    rows = list(rows)
    if path == "-":
        for r in rows:
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(rows)
    p = Path(path).resolve()
    if PRIVATE.resolve() not in p.parents:
        raise SystemExit(f"отказ: приватные результаты пишем только в {PRIVATE} (gitignore), не в {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return float(v[min(len(v) - 1, int(q * len(v)))])


def report(title: str, data: Dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
