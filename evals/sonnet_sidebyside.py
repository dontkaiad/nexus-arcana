"""evals/sonnet_sidebyside.py — Sonnet 4.6 vs 5.5 «рядом» на РЕАЛЬНЫХ промптах бота (читает человек, метрики нет).

    python -m evals.sonnet_sidebyside tarot --n 3  > data/evals/sonnet_sidebyside_<дата>.md
    python -m evals.sonnet_sidebyside budget --runs 2 >> ...

tarot  — последние N раскладов владельца: тот же системный промпт и тот же формат запроса, что в боте в режиме «сгенерить
         трактовку» (TAROT_SYSTEM + справочник карт), без памяти/прошлых раскладов/RAG-голоса. Каждая трактовка — тремя
         вариантами: Sonnet 4.6 (как сейчас), Sonnet 5.5 (мышление по умолчанию) и 5.5 с мышлением low.
budget — полный бюджетный разбор на ТЕКУЩИХ данных (тот же вход `_build_sonnet_input` и BUDGET_SONNET_SYSTEM): JSON-план,
         детерминированный пересчёт лимитов, пересказ — для каждой модели `--runs` раз подряд (видно разброс между
         запусками: у 5.5 нет temperature). Показывается то, что увидела бы Кай (`_format_plan`).
Ничего не пишет в БД и не шлёт в Telegram. Вывод — markdown в stdout: ПРИВАТНЫЙ, перенаправлять только в data/evals/.
Для каждого варианта: латентность, токены вход/выход (выход включает мышление).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from typing import Any, Dict, List

import sqlalchemy as sa

A_MODEL = "claude-sonnet-4-6"
B_MODEL = "claude-sonnet-5-5"


def _split_cards(cards: str) -> List[str]:
    return [c.strip() for c in re.split(r"[,;\n/]+", cards or "") if c.strip()]


def _set_role(role: str, model: str, thinking: str = "") -> None:
    os.environ[f"MODEL_{role.upper()}"] = model
    if thinking:
        os.environ[f"THINKING_{role.upper()}"] = thinking
    else:
        os.environ.pop(f"THINKING_{role.upper()}", None)


async def _ask(prompt: str, system: str, role: str, max_tokens: int, temperature: float) -> Dict[str, Any]:
    from core.claude_client import ask_claude
    usage: Dict[str, int] = {}
    t0 = time.monotonic()
    text = await ask_claude(prompt, system=system, role=role, max_tokens=max_tokens, temperature=temperature,
                            usage_out=usage, label=f"sidebyside:{role}")
    return {"text": text, "ms": int((time.monotonic() - t0) * 1000), "in": usage.get("input_tokens", 0),
            "out": usage.get("output_tokens", 0)}


def _stat(r: Dict[str, Any]) -> str:
    return f"_{r['ms']} мс · вход {r['in']} · выход {r['out']} токенов_"


VARIANTS = [("Sonnet 4.6 (сейчас)", A_MODEL, ""), ("Sonnet 5.5 (мышление по умолчанию)", B_MODEL, ""),
            ("Sonnet 5.5 (мышление low)", B_MODEL, "low")]


async def tarot(n: int) -> str:
    from arcana.handlers.sessions import TAROT_SYSTEM
    from arcana.tarot_loader import get_cards_context
    from core.db import get_engine
    with get_engine().connect() as c:
        uid = c.execute(sa.text("SELECT user_id FROM sessions WHERE NOT coalesce(archived, false) "
                                "GROUP BY user_id ORDER BY count(*) DESC LIMIT 1")).scalar()
        rows = c.execute(sa.text(
            "SELECT id, question, cards, bottom_card, deck, area FROM sessions WHERE user_id = :u "
            "AND NOT coalesce(archived, false) AND coalesce(cards, '') <> '' "
            "ORDER BY occurred_at DESC NULLS LAST, id DESC LIMIT :n"), {"u": uid, "n": n}).all()
    out = [f"# Трактовки: Sonnet 4.6 vs 5.5 ({len(rows)} последних раскладов)\n",
           "_Промпт как в боте (режим «сгенерить»), без памяти / прошлых раскладов / RAG-голоса. "
           "Температура 0.7 только у 4.6 (у 5.5 её нет)._\n"]
    for i, r in enumerate(rows, 1):
        names = _split_cards(r.cards) + ([r.bottom_card] if r.bottom_card else [])
        ctx = get_cards_context(r.deck or "", names) if r.deck else ""
        system = TAROT_SYSTEM + (f"\n\n--- СПРАВОЧНИК КАРТ ---\n{ctx}" if ctx else "")
        prompt = f"Расклад: {r.area or ''}\nВопрос: {r.question or ''}\nКарты: {r.cards}"
        if r.bottom_card:
            prompt += f"\nДно колоды: {r.bottom_card}"
        out.append(f"\n## Расклад {i} (id {r.id})\n\n**Вопрос:** {r.question or '—'}  \n**Карты:** {r.cards}"
                   + (f"  \n**Дно:** {r.bottom_card}" if r.bottom_card else "") + "\n")
        for title, model, thinking in VARIANTS:
            _set_role("narrative", model, thinking)
            print(f"tarot {i}/{len(rows)}: {title}", file=sys.stderr)
            res = await _ask(prompt, system, "narrative", 2000, 0.7)
            out.append(f"\n### {title}\n{_stat(res)}\n\n{res['text'].strip() or '_(пустой ответ)_'}\n")
    return "\n".join(out)


async def budget(runs: int) -> str:
    from core.config import config
    from nexus.handlers import finance as F
    from core.db import get_engine
    with get_engine().connect() as c:
        user_id = c.execute(sa.text("SELECT user_id FROM memories WHERE is_current AND NOT is_archived "
                                    "GROUP BY user_id ORDER BY count(*) DESC LIMIT 1")).scalar()
    uid = config.allowed_ids[0]
    sonnet_input = await F._build_sonnet_input(uid, user_id)
    out = ["# Бюджетный разбор: Sonnet 4.6 vs 5.5\n",
           f"_Вход — текущие данные (≈{len(sonnet_input)} символов), системный промпт BUDGET_SONNET_SYSTEM. "
           f"Каждая модель — {runs} запуска подряд, чтобы видеть разброс._\n"]
    for title, model, thinking in VARIANTS:
        for run in range(1, runs + 1):
            _set_role("analysis", model, thinking)
            print(f"budget {title} run {run}", file=sys.stderr)
            res = await _ask(sonnet_input, F.BUDGET_SONNET_SYSTEM, "analysis", 4096, 0)
            raw = (res["text"] or "").strip()
            m = re.search(r"\{[\s\S]*\}", raw)
            plan, err = None, ""
            try:
                plan = json.loads(m.group(0)) if m else None
                if plan is None:
                    err = "в ответе нет JSON"
            except Exception as e:
                err = f"JSON не разобрался: {type(e).__name__}"
            out.append(f"\n## {title} · запуск {run}\n{_stat(res)}\n")
            if plan is None:
                out.append(f"\n**План не получен:** {err}\n\n```\n{raw[:1500]}\n```\n")
                continue
            t1 = time.monotonic()
            try:
                F._apply_computed_limits(plan)
                await F._budget_phase2_narration(plan)
                shown = F._format_plan(plan)
            except Exception as e:
                shown = f"(ошибка пост-обработки: {type(e).__name__}: {e})"
            out.append(f"\n_пересчёт лимитов + пересказ: {int((time.monotonic() - t1) * 1000)} мс_\n\n{shown}\n")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(prog="evals.sonnet_sidebyside")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tarot"); t.add_argument("--n", type=int, default=3)
    b = sub.add_parser("budget"); b.add_argument("--runs", type=int, default=2)
    a = ap.parse_args()
    os.environ["MEMORY_ANSWER_LOG"] = "0"
    text = asyncio.run(tarot(a.n) if a.cmd == "tarot" else budget(a.runs))
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
