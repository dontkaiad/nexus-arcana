"""scripts/llm_usage_stats.py — сводка по журналу llm_call_log (READ-ONLY, только числа): $, токены, кеш, латентность.

    docker exec -i nexus-bot python - [--days 7] [--by label|model] < scripts/llm_usage_stats.py

По метке вызова (модуль.функция) и модели: вызовов, токены вход / запись в кеш / чтение из кеша / выход, доля кеша
(чтение ÷ весь вход), $ за период и прогноз на 30 дней, латентность p50 / p95. Цены — таблица PRICES ниже
(за 1M токенов; запись в кеш 1.25× входа для 5m и 2× для 1h, чтение 0.1×); сверять с прайсом Anthropic.
"""
from __future__ import annotations

import argparse

# (вход, выход) $/1M токенов по префиксу модели
PRICES = [
    ("claude-haiku-5", (0.10, 0.50)),
    ("claude-haiku-4", (1.00, 5.00)),
    ("claude-sonnet-5", (2.00, 10.00)),
    ("claude-sonnet-4", (3.00, 15.00)),
    ("claude-opus-5-5", (4.00, 20.00)),
    ("claude-opus", (5.00, 25.00)),
]


def price(model: str):
    for pref, p in PRICES:
        if (model or "").startswith(pref):
            return p
    return (0.0, 0.0)


def cost(model: str, i: int, cw: int, cr: int, o: int, ttl_1h_share: float = 0.0) -> float:
    pin, pout = price(model)
    write_mult = 1.25 * (1 - ttl_1h_share) + 2.0 * ttl_1h_share
    return (i * pin + cw * pin * write_mult + cr * pin * 0.10 + o * pout) / 1_000_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--by", choices=["label", "model"], default="label")
    args = ap.parse_args()
    import sqlalchemy as sa
    from core.db import get_engine
    with get_engine().connect() as c:
        rows = c.execute(sa.text("""
            SELECT label, model, count(*) AS n, sum(in_tokens) AS i, sum(cache_write_tokens) AS cw,
                   sum(cache_read_tokens) AS cr, sum(out_tokens) AS o,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
                   count(*) FILTER (WHERE NOT ok) AS fails,
                   avg((cache_ttl = '1h')::int) AS ttl1h
            FROM llm_call_log WHERE ts > now() - make_interval(days => :d)
            GROUP BY label, model ORDER BY label, model"""), {"d": args.days}).all()
    total = 0.0
    print(f"{'вызов':44} {'модель':22} {'n':>5} {'вход':>8} {'кеш-зап':>8} {'кеш-чт':>8} {'вых':>7} {'кеш%':>5} {'$':>8} {'p50мс':>6} {'p95мс':>6}")
    for r in rows:
        allin = (r.i or 0) + (r.cw or 0) + (r.cr or 0)
        hit = 100 * (r.cr or 0) // allin if allin else 0
        usd = cost(r.model, r.i or 0, r.cw or 0, r.cr or 0, r.o or 0, float(r.ttl1h or 0))
        total += usd
        print(f"{r.label[:44]:44} {r.model[:22]:22} {r.n:5} {r.i or 0:8} {r.cw or 0:8} {r.cr or 0:8} {r.o or 0:7} {hit:4}% "
              f"{usd:8.4f} {int(r.p50 or 0):6} {int(r.p95 or 0):6}" + (f"  сбоев: {r.fails}" if r.fails else ""))
    print(f"\nитого за {args.days} дн.: ${total:.4f}  → прогноз на 30 дней: ${total / args.days * 30:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
