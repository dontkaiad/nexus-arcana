"""python -m evals offline — все офлайн-эвалы одной командой (нужен Postgres, как в CI; без LLM и сети)."""
from __future__ import annotations

import json
import sys

from evals import cat_health_eval, classifier_eval, grounding_eval, memory_eval


def run_all():
    return {"classifier": classifier_eval.run_offline(), "memory_retrieval": memory_eval.run_offline(),
            "grounding": grounding_eval.run_offline(), "cat_health_input": cat_health_eval.run_offline()}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "offline":
        print(__doc__)
        return 2
    res = run_all()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    ok = (res["classifier"]["accuracy_pct"] == 100.0 and res["memory_retrieval"]["recall_at_5_pct"] == 100.0
          and res["memory_retrieval"]["ambiguity_acc_pct"] in (None, 100.0)
          and res["grounding"]["accept_rate_pct"] == 100.0 and res["grounding"]["reject_recall_pct"] == 100.0
          and res["cat_health_input"]["outcome_acc_pct"] == 100.0 and not res["cat_health_input"]["false_health"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
