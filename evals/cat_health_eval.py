"""evals/cat_health_eval.py — ввод здоровья котов из чата («Тучка весит 4,2 кг», «Кекс ветеринар 12 декабря», «Кексу капли с 15-го на 10 дней»).

Что измеряется (на сообщение):
  gate_acc     — проверка без LLM (`wants_health`): блок про здоровье добавляется в запрос только если в тексте есть имя кота и слово про здоровье;
  outcome_acc  — итог сообщения: weight | visit | med | fact («fact» = сохраняется обычным фактом, как раньше). Для weight / visit / med ещё
                 совпадение граммов / даты / вида / числа дней с ожидаемым;
  false_health — сообщения, которые должны остаться фактом, а ушли в вес / визит / лекарство (самая вредная ошибка: запись, которой не было);
  missed       — сообщения про здоровье кота, ушедшие фактом.
Режимы:
  offline — синтетика evals/synthetic/cat_health_cases.jsonl: поле "model" — ответ Haiku, как его ждёт промпт (вписан руками); проверка без LLM
            и сети проходит gate, строгую проверку `validate` и резолв кота по имени. Это регрессия кода, а не оценка модели.
  live    — то же, но ответ даёт настоящий Haiku по настоящему промпту (`core.memory._parse_fact`, блок здоровья): оценка модели. Нужен
            ANTHROPIC_API_KEY; в сеть уходит только вымышленный текст синтетики. Стоит центы.
«До» (без этой функции) по определению: итог всегда fact → outcome_acc = доля «fact» в наборе, missed = все сообщения про здоровье.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date
from typing import Any, Dict, List, Optional

from evals.common import SYNTHETIC, load_jsonl, pct, report

CATS = ["Тучка", "Пончик", "Кекс"]
TODAY = date(2026, 11, 10)                 # вторник; все ожидаемые даты в наборе считаются от него


def _resolve(name: str) -> Optional[str]:
    hits = [c for c in CATS if c.casefold() == (name or "").strip().casefold()]
    return hits[0] if len(hits) == 1 else None


def outcome(case: Dict[str, Any], model_obj: Any) -> Dict[str, Any]:
    """Итог по тому же пути, что в core.memory.parse_and_store: gate → строгая проверка → резолв кота (ровно один)."""
    from core import cat_health_chat as chat
    if not chat.wants_health(case["text"], CATS):
        return {"gate": False, "outcome": "fact"}
    item = chat.validate(model_obj, TODAY, case["text"])
    if item is None or _resolve(item.cat_name) is None:
        return {"gate": True, "outcome": "fact"}
    out: Dict[str, Any] = {"gate": True, "outcome": item.type, "grams": item.grams, "date": item.on.isoformat() if item.on else None,
                           "kind": item.kind, "days": item.days}
    return out


def _details_ok(case: Dict[str, Any], got: Dict[str, Any]) -> bool:
    if got["outcome"] != case["expect"]:
        return False
    for k in ("grams", "date", "kind", "days"):
        if k in case and got.get(k) != case[k]:
            return False
    return True


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    health = [r for r in rows if r["case"]["expect"] != "fact"]
    facts = [r for r in rows if r["case"]["expect"] == "fact"]
    ok = sum(1 for r in rows if _details_ok(r["case"], r["got"]))
    false_h = [r["case"]["id"] for r in facts if r["got"]["outcome"] != "fact"]
    missed = [r["case"]["id"] for r in health if r["got"]["outcome"] == "fact"]
    wrong = [{"id": r["case"]["id"], "got": r["got"], "expected": r["case"]["expect"]} for r in rows if not _details_ok(r["case"], r["got"])]
    return {"cases": len(rows), "health_cases": len(health), "fact_cases": len(facts), "outcome_acc_pct": pct(ok, len(rows)),
            "false_health": false_h, "missed": missed, "before_outcome_acc_pct": pct(len(facts), len(rows)),
            "before_missed": len(health), "wrong": wrong}


# Детерминированный префильтр классификатора «сделали / сделала … → task_done»: (текст, должен ли дойти до модели). Слова про здоровье животного
# его обходят (запись о здоровье кота, а не отметка задачи), обычные «сделала …» по-прежнему не доходят до LLM.
PREFILTER = [("Тучке сделали прививку 5 ноября", True), ("Пончику сделала прививку от бешенства", True), ("Кексу сделал укол и дал капли", True),
             ("сделала покупку корма", False), ("выполнила задачу про отчёт", False), ("закончила уборку", False)]


def prefilter_check() -> Dict[str, Any]:
    from unittest.mock import AsyncMock, patch
    from core import classifier
    wrong = []
    for text, reaches in PREFILTER:
        llm = AsyncMock(return_value='[{"type":"memory_save","text":"x"}]')
        with patch.object(classifier, "ask_claude", llm), patch.object(classifier, "_known_budget_positions", AsyncMock(return_value=([], []))):
            asyncio.run(classifier.classify(text, tz_offset=3, user_id=""))
        if (llm.await_count > 0) != reaches:
            wrong.append(text)
    return {"prefilter_cases": len(PREFILTER), "prefilter_acc_pct": pct(len(PREFILTER) - len(wrong), len(PREFILTER)), "prefilter_wrong": wrong}


def run_offline() -> Dict[str, Any]:
    cases = load_jsonl(str(SYNTHETIC / "cat_health_cases.jsonl"))
    out = summarize([{"case": c, "got": outcome(c, c.get("model"))} for c in cases])
    out.update(prefilter_check())
    return out


async def _live(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    from core import cat_health_chat as chat
    from core import memory
    rows = []
    for c in cases:
        model_obj = None
        if chat.wants_health(c["text"], CATS):
            parsed = await memory._parse_fact(c["text"], CATS, health=TODAY)
            model_obj = getattr(parsed, "health", None)
        rows.append({"case": c, "got": outcome(c, model_obj), "model": model_obj})
    s = summarize(rows)
    s["raw_model_answers"] = {r["case"]["id"]: r["model"] for r in rows if r["model"] is not None}
    return s


def run_live() -> Dict[str, Any]:
    if not os.environ.get("ANTHROPIC_API_KEY") or os.environ["ANTHROPIC_API_KEY"].startswith("fake"):
        raise SystemExit("live: нужен настоящий ANTHROPIC_API_KEY")
    return asyncio.run(_live(load_jsonl(str(SYNTHETIC / "cat_health_cases.jsonl"))))


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.cat_health_eval")
    ap.add_argument("mode", choices=["offline", "live"])
    a = ap.parse_args()
    report(f"cat health input ({a.mode}, синтетика)", run_offline() if a.mode == "offline" else run_live())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
