"""scripts/trace_show.py — трассы вопросов к памяти (таблица memory_trace). READ-ONLY, без LLM.

    docker exec -i nexus-bot python - 123        < scripts/trace_show.py   # одна трасса по id
    docker exec -i nexus-bot python - last 5     < scripts/trace_show.py   # последние N
    docker exec -i nexus-bot python - --stats    < scripts/trace_show.py   # счётчики за 7 дней (--days N)
    … последний аргумент --texts подставляет тексты фактов к id (только в твоём терминале; по умолчанию — только id)

Цель: когда бот ответил не то — за минуту увидеть, на каком шаге сломалось (разбор → FTS/вектор → слияние → реранк →
генерация). В таблице тексты фактов не хранятся, только id; сам вопрос — исходный текст.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List


def verdict(r: Dict[str, Any]) -> str:
    """Первый подозрительный шаг цепочки — одной строкой."""
    if r.get("error"):
        return f"исключение {r['error']} — смотри лог бота на это время"
    if r.get("route") == "unknown_person":
        return "разбор: имя не найдено среди людей/котов → «не знаю такого»"
    fts, vec = r.get("fts"), r.get("vec")
    if r.get("route") in ("direct", "birthday"):
        return "прямой запрос по карточке (без поиска): " + ("пусто — у человека нет фактов этого типа" if not r.get("final_ids") else "ок")
    if fts is not None and not fts and not vec:
        return "поиск: ни FTS, ни вектор ничего не нашли (фильтр субъекта слишком узкий или фактов нет)"
    if vec is not None and not vec and fts:
        return "вектор: пусто при непустом FTS (Voyage недоступен/ключ?)"
    rr = r.get("rerank") or {}
    if rr.get("failed"):
        return f"реранк упал ({rr['failed']}) → остался порядок RRF"
    if rr.get("ran") and r.get("rrf_ids") and not rr.get("ids"):
        return "реранк отсёк ВСЕХ кандидатов (ответ «не нашёл»)"
    gen = r.get("gen")
    if gen and gen.get("attempted") and not gen.get("ok"):
        return f"генерация: fallback на список ({gen.get('reason')}{'/check ' + str(gen['check']) if gen.get('check') else ''})"
    if not r.get("final_ids"):
        return "ничего не нашлось"
    return "цепочка отработала без сбоев"


def _texts(ids: List[int]) -> Dict[int, str]:
    import sqlalchemy as sa
    from core.db import get_engine
    if not ids:
        return {}
    with get_engine().connect() as c:
        return {r.id: r.fact_text for r in c.execute(sa.text("SELECT id, fact_text FROM memories WHERE id = ANY(:i)"), {"i": list(ids)})}


def show(r: Dict[str, Any], texts: bool = False) -> None:
    ids_all = set()
    for k in ("rrf_ids", "final_ids"):
        ids_all |= set(r.get(k) or [])
    ids_all |= {x[0] for x in (r.get("fts") or [])} | {x[0] for x in (r.get("vec") or [])}
    tx = _texts(sorted(ids_all)) if texts else {}
    name = lambda i: f"#{i}" + (f" «{tx.get(i, '?')[:70]}»" if texts else "")                  # noqa: E731
    print(f"── трасса {r['id']} · {r['ts']:%Y-%m-%d %H:%M:%S} · {r['bot']} · {r.get('total_ms')} мс")
    print(f"вопрос:  {r['question']}")
    print(f"разбор:  subject={r.get('subject')} types={r.get('types') or '-'} intent={r.get('intent') or '-'} "
          f"route={r.get('route')} kind={r.get('kind')} parse={r.get('parse')}")
    fts, vec = r.get("fts"), r.get("vec")
    if fts is not None:
        print(f"FTS ({len(fts)}):    " + ", ".join(f"{name(i)}@{rk}" for i, rk in fts[:12]) + (" …" if len(fts) > 12 else ""))
        print(f"вектор ({len(vec or [])}): " + ", ".join(f"{name(i)}@{rk}({sc})" for i, rk, sc in (vec or [])[:12]) + (" …" if len(vec or []) > 12 else ""))
        print(f"RRF:     {len(r.get('rrf_ids') or [])} кандидатов: " + ", ".join(name(i) for i in (r.get('rrf_ids') or [])[:12]))
    rr = r.get("rerank")
    if rr and rr.get("ran"):
        print(f"реранк:  выбрал {rr.get('ids')}" + (f" · СБОЙ {rr['failed']}" if rr.get("failed") else ""))
    print(f"итог:    {', '.join(name(i) for i in (r.get('final_ids') or [])) or '— пусто'}")
    g = r.get("gen")
    if g:
        print("генерация: " + ("ok, использовала " + str(g.get("used_ids")) if g.get("ok") else
                               f"fallback reason={g.get('reason')} check={g.get('check')} bad_ids={g.get('bad_ids')}"))
    print("шаги, мс: " + ", ".join(f"{k}={v}" for k, v in (r.get("latency") or {}).items()))
    print(f"ВЕРДИКТ: {verdict(r)}\n")


def stats(days: int) -> None:
    import sqlalchemy as sa
    from core.db import get_engine
    with get_engine().connect() as c:
        day = c.execute(sa.text(
            "SELECT ts::date d, count(*) n, count(*) FILTER (WHERE kind = 'none' OR final_ids IS NULL OR cardinality(final_ids) = 0) empty, "
            "count(*) FILTER (WHERE rerank->>'failed' IS NOT NULL) rr_fail, count(*) FILTER (WHERE error IS NOT NULL) err, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) p50, percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) p95 "
            "FROM memory_trace WHERE ts > now() - make_interval(days => :d) GROUP BY 1 ORDER BY 1 DESC"), {"d": days}).all()
        routes = c.execute(sa.text(
            "SELECT route, subject, count(*) n FROM memory_trace WHERE ts > now() - make_interval(days => :d) "
            "GROUP BY 1, 2 ORDER BY 3 DESC"), {"d": days}).all()
        gen = c.execute(sa.text(
            "SELECT COALESCE(gen->>'reason', CASE WHEN (gen->>'ok')::bool THEN 'ok' END, '-') r, count(*) n FROM memory_trace "
            "WHERE gen IS NOT NULL AND ts > now() - make_interval(days => :d) GROUP BY 1 ORDER BY 2 DESC"), {"d": days}).all()
        steps = c.execute(sa.text(
            "SELECT k, round(avg(v::int)) FROM memory_trace, jsonb_each_text(latency) AS e(k, v) "
            "WHERE ts > now() - make_interval(days => :d) GROUP BY 1 ORDER BY 2 DESC"), {"d": days}).all()
    print(f"счётчики за {days} дн.\nдень        вопросов  пусто  реранк-сбой  ошибок  p50мс  p95мс")
    for r in day:
        print(f"{r.d}  {r.n:8}  {r.empty:5}  {r.rr_fail:11}  {r.err:6}  {int(r.p50 or 0):5}  {int(r.p95 or 0):5}")
    print("\nroute / subject:")
    for r in routes:
        print(f"  {r.route or '-':16} {r.subject or '-':15} {r.n}")
    print("\nгенерация (итог / причина fallback):")
    for r in gen:
        print(f"  {r.r:16} {r.n}")
    print("\nсредняя латентность шагов (мс от предыдущего события):")
    for k, v in steps:
        print(f"  {k:14} {int(v)}")


def main(argv: List[str]) -> int:
    texts = "--texts" in argv
    argv = [a for a in argv if a != "--texts"]
    import sqlalchemy as sa
    from core.db import get_engine
    if argv and argv[0] == "--stats":
        days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 7
        stats(days)
        return 0
    if argv and argv[0] == "last":
        n = int(argv[1]) if len(argv) > 1 else 5
        q, p = "SELECT * FROM memory_trace ORDER BY id DESC LIMIT :n", {"n": n}
    elif argv and argv[0].isdigit():
        q, p = "SELECT * FROM memory_trace WHERE id = :i", {"i": int(argv[0])}
    else:
        print(__doc__)
        return 2
    with get_engine().connect() as c:
        rows = c.execute(sa.text(q), p).mappings().all()
    if not rows:
        print("трасс нет")
    for r in reversed(rows) if argv[0] == "last" else rows:
        show(dict(r), texts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
