"""evals/cat_block_eval.py — блок факта про кота при записи («Тучка рыжая…» → profile, «Пончику нельзя курицу» → health, «обошлось в 12 тысяч» → owner).

Что измеряется (на сообщение):
  block_acc    — блок совпал с ожидаемым;
  audience_acc — аудитория (sitter | owner) совпала; аудиторию определяет `cat_blocks.audience(блок, тип факта)`, тип — тем же `guess_fact_type`, что при записи;
  false_open   — закрытый для ситтера факт получил открытую аудиторию (самая вредная ошибка: личное или «неразобранное» ушло бы ситтеру). Должно быть 0;
  missed_open  — факт, который ситтеру нужен, остался закрытым (допустимо: владелец разметит руками, но считаем);
  unsorted_pct — доля сообщений, ушедших в «неразобранное» (сомнение, нет ответа модели, ответ вне списка, дешёвая проверка не попросила блок).
Режимы:
  offline — синтетика evals/synthetic/cat_block_cases.jsonl: поле "model" — ответ Haiku на ключ «блок», как его ждёт промпт (вписан руками, два ответа нарочно
            мусорные); проверка без LLM и сети проходит `wants_block`, `normalize` и `audience`. Это регрессия кода, а не оценка модели.
  live    — то же, но ответ даёт настоящий Haiku по настоящему промпту (`core.memory._parse_fact`, блок). Нужен ANTHROPIC_API_KEY; в сеть уходит только
            вымышленный текст синтетики. Стоит центы.
«До» (без этой функции) по определению: блока нет (NULL = закрыто) → false_open = 0, block_acc = 0, missed_open = все факты, нужные ситтеру.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Dict, List, Optional

from core import cat_blocks
from core.people import guess_fact_type
from evals.common import SYNTHETIC, load_jsonl, pct, report

CATS = ["Тучка", "Пончик", "Кекс"]


def got_block(case: Dict[str, Any], model_answer: Optional[str]) -> str:
    """Тот же путь, что в core.memory.parse_and_store: дешёвая проверка → строгая нормализация (нет ответа / вне списка → unsorted)."""
    if not cat_blocks.wants_block(case["text"], CATS):
        return "unsorted"
    return cat_blocks.normalize(model_answer)


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    for r in rows:
        t = guess_fact_type(r["case"]["text"])
        r["want_aud"] = cat_blocks.audience(r["case"]["expect"], t)
        r["got_aud"] = cat_blocks.audience(r["got"], t)
    n = len(rows)
    sitter_needed = [r for r in rows if r["want_aud"] == "sitter"]
    return {
        "cases": n, "sitter_cases": len(sitter_needed),
        "block_acc_pct": pct(sum(r["got"] == r["case"]["expect"] for r in rows), n),
        "audience_acc_pct": pct(sum(r["got_aud"] == r["want_aud"] for r in rows), n),
        "false_open": [r["case"]["id"] for r in rows if r["want_aud"] == "owner" and r["got_aud"] == "sitter"],
        "missed_open": [r["case"]["id"] for r in sitter_needed if r["got_aud"] == "owner"],
        "unsorted_pct": pct(sum(r["got"] == "unsorted" for r in rows), n),
        "before_block_acc_pct": 0.0, "before_false_open": 0, "before_missed_open": len(sitter_needed),
        "wrong": [{"id": r["case"]["id"], "got": r["got"], "expected": r["case"]["expect"]} for r in rows if r["got"] != r["case"]["expect"]],
    }


def run_offline() -> Dict[str, Any]:
    cases = load_jsonl(str(SYNTHETIC / "cat_block_cases.jsonl"))
    return summarize([{"case": c, "got": got_block(c, c.get("model"))} for c in cases])


async def _live(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    from core import memory
    rows = []
    for c in cases:
        ans = None
        if cat_blocks.wants_block(c["text"], CATS):
            parsed = await memory._parse_fact(c["text"], CATS, block=True)
            ans = getattr(parsed, "block", None) if parsed[1] == memory.CATS_CATEGORY else None
        rows.append({"case": c, "got": got_block(c, ans), "model": ans})
    s = summarize(rows)
    s["raw_model_answers"] = {r["case"]["id"]: r["model"] for r in rows if r["model"] is not None}
    return s


def run_live() -> Dict[str, Any]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or key.startswith("fake"):
        raise SystemExit("live: нужен настоящий ANTHROPIC_API_KEY")
    return asyncio.run(_live(load_jsonl(str(SYNTHETIC / "cat_block_cases.jsonl"))))


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.cat_block_eval")
    ap.add_argument("mode", choices=["offline", "live"])
    a = ap.parse_args()
    report(f"cat fact block ({a.mode}, синтетика)", run_offline() if a.mode == "offline" else run_live())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
