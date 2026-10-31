"""scripts/memory_answer_stats.py — доля fallback генерации ответов по дням (READ-ONLY, только числа).

    docker exec -i nexus-bot python - [--days 7] < scripts/memory_answer_stats.py

По дням: успехов / fallback, процент fallback, разбивка по причинам (timeout, api_error, check_1…check_6),
латентность p50/max (мс) и токены вход/выход в среднем. Какая проверка срабатывает чаще всего — по ней
решаем, ослаблять ли её (4c — значимые слова — первая кандидатка).
"""
from __future__ import annotations

import argparse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    import sqlalchemy as sa
    from core.db import get_engine
    with get_engine().connect() as c:
        rows = c.execute(sa.text("""
            SELECT ts::date AS day, bot, count(*) FILTER (WHERE outcome='ok') AS ok,
                   count(*) FILTER (WHERE outcome='fallback') AS fb,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                   max(latency_ms) AS mx, round(avg(in_tokens)) AS tin, round(avg(out_tokens)) AS tout
            FROM memory_answer_log WHERE ts > now() - make_interval(days => :d)
            GROUP BY 1, 2 ORDER BY 1 DESC, 2"""), {"d": args.days}).all()
        reasons = c.execute(sa.text("""
            SELECT ts::date AS day, reason, count(*) FROM memory_answer_log
            WHERE outcome='fallback' AND ts > now() - make_interval(days => :d)
            GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC"""), {"d": args.days}).all()
    print("день       бот     ok  fallback  fb%   p50мс  maxмс  токены(вх/вых)")
    for r in rows:
        tot = r.ok + r.fb
        print(f"{r.day}  {r.bot:7} {r.ok:3}  {r.fb:8}  {100*r.fb//tot if tot else 0:3}%  {int(r.p50 or 0):6} {r.mx or 0:6}  {int(r.tin or 0)}/{int(r.tout or 0)}")
    print("\nпричины fallback:")
    for r in reasons:
        print(f"  {r.day}  {r.reason or '-':10} {r[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
