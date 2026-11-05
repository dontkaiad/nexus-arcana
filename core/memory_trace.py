"""core/memory_trace.py — трассировка вопроса к памяти в таблицу memory_trace (одна строка на вопрос).

`Tracer` — вызываемый объект `trace(step, data)`, который уже понимают `memory_search.answer` и `memory_answer.generate`.
Копит события, считает время каждого шага и в конце `finish()` пишет ОДНУ строку: исходный вопрос, разбор, кандидаты
FTS/вектора с рангами, выбор реранка, итоговые id, итог генерации / причину fallback, латентность по шагам.
Тексты фактов не пишутся — только id. Любой сбой записи молча проглатывается: трассировка не должна ломать ответ.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.memory_trace")


def _ms(delta: float) -> int:
    """Секунды → мс, в разумных пределах (часы могут быть подменены/сдвинуты — отрицательное и гигантское не пишем)."""
    return max(0, min(int(delta * 1000), 3_600_000))


class Tracer:
    def __init__(self, bot: str, user_id: str, question: str) -> None:
        self.bot, self.user_id, self.question = bot, user_id or "", (question or "").strip()
        self._t0 = self._last = time.monotonic()
        self.events: List[tuple] = []                 # (step, data, ms_since_prev)

    def __call__(self, step: str, data: Optional[dict] = None) -> None:
        now = time.monotonic()
        self.events.append((step, data or {}, _ms(now - self._last)))
        self._last = now

    def _ev(self, step: str) -> Optional[dict]:
        for s, d, _ in self.events:
            if s == step:
                return d
        return None

    def row(self, error: Optional[BaseException] = None) -> Dict[str, Any]:
        parse, legs, rrf = self._ev("parse") or {}, self._ev("legs") or {}, self._ev("rrf")
        rer, res = self._ev("rerank"), self._ev("result") or {}
        rfail = self._ev("rerank_failed")
        direct = self._ev("direct")
        gen_in, gen_out, gen_fb = self._ev("gen_input"), self._ev("gen_output"), self._ev("gen_fallback")
        vec_ids, scores = legs.get("vec") or [], legs.get("vec_scores") or []
        latency: Dict[str, int] = {}
        for s, _, ms in self.events:
            latency[s] = latency.get(s, 0) + ms
        gen: Optional[dict] = None
        if gen_in is not None or gen_out is not None or gen_fb is not None:
            gen = {"attempted": gen_in is not None, "ok": gen_out is not None,
                   "reason": (gen_fb or {}).get("reason"), "check": (gen_fb or {}).get("check"),
                   "used_ids": (gen_out or {}).get("used_ids") or [], "bad_ids": (gen_fb or {}).get("ids") or []}
        return {
            "bot": self.bot, "user_id": self.user_id, "question": self.question,
            "subject": res.get("subject") or parse.get("subject"), "types": list(res.get("types") or parse.get("types") or []),
            "intent": parse.get("intent"), "route": res.get("route"), "kind": res.get("kind"),
            "parse": {"people": parse.get("people") or [], "cats": parse.get("cats") or [],
                      **({"direct_ids": direct.get("ids")} if direct else {})} if parse or direct else None,
            "fts": [[i, r + 1] for r, i in enumerate(legs.get("fts") or [])] if legs else None,
            "vec": [[i, r + 1, round(scores[r], 3) if r < len(scores) else None] for r, i in enumerate(vec_ids)] if legs else None,
            "rrf_ids": (rrf or {}).get("ids"),
            "rerank": ({"ran": True, "ids": rer.get("ids"), "failed": (rfail or {}).get("reason")} if rer is not None or rfail
                       else {"ran": False, "ids": None, "failed": None}) if legs else None,
            "final_ids": res.get("ids"), "gen": gen, "latency": latency,
            "total_ms": _ms(time.monotonic() - self._t0),
            "error": type(error).__name__ if error else None,
        }

    def finish(self, error: Optional[BaseException] = None) -> None:
        try:
            r = self.row(error)
            import sqlalchemy as sa
            from core.db import get_engine
            j = lambda v: None if v is None else json.dumps(v, ensure_ascii=False)       # noqa: E731
            with get_engine().begin() as c:
                c.execute(sa.text(
                    "INSERT INTO memory_trace (bot, user_id, question, subject, types, intent, route, kind, parse, fts, vec, "
                    "rrf_ids, rerank, final_ids, gen, latency, total_ms, error) VALUES (:bot, :user_id, :question, :subject, "
                    ":types, :intent, :route, :kind, CAST(:parse AS jsonb), CAST(:fts AS jsonb), CAST(:vec AS jsonb), :rrf_ids, "
                    "CAST(:rerank AS jsonb), :final_ids, CAST(:gen AS jsonb), CAST(:latency AS jsonb), :total_ms, :error)"),
                    {**r, "parse": j(r["parse"]), "fts": j(r["fts"]), "vec": j(r["vec"]), "rerank": j(r["rerank"]),
                     "gen": j(r["gen"]), "latency": j(r["latency"])})
        except Exception:
            logger.warning("memory_trace: запись не удалась", exc_info=True)
