"""scripts/memory_search_eval.py — срез top-5 поиска по 🧠 Память «до/после» (READ-ONLY).

Печатает ТОЛЬКО route / id / тип / категорию / score — без текстов фактов (там личные данные).
Ничего не пишет в БД. Самодостаточен: каждый режим импортирует только своё, поэтому режим
`hybrid` можно запускать внутри контейнера со СТАРЫМ кодом (этап 1), не накатывая ветку.

    docker exec -i nexus-bot python - --mode flow < scripts/memory_search_eval.py     # РЕАЛЬНЫЙ путь сообщения
    docker exec -i nexus-bot python - --mode hybrid < scripts/memory_search_eval.py   # этап 1 (только гибрид)
    docker exec -i nexus-bot python - --mode answer < scripts/memory_search_eval.py   # answer() напрямую

--user-id не задан → владелец с наибольшим числом фактов (сам id не печатается).
--mode old    : до этапа 1 (ILIKE-OR + semantic-фоллбэк); нужен код до 68a0475.
--mode hybrid : core.memory_search.hybrid_search(k=5, Haiku-реранк).
--mode answer : core.memory_search.answer() — понимание вопроса → прямой запрос / гибрид.
--mode flow   : ТОЧНО как в боте: текст сообщения → core.classifier.classify (regex-префильтр или реальный
                Haiku) → process_item → хэндлер → search_memory → answer(). Отправка в Telegram подменена
                заглушкой (ничего не уходит), выполняется ТОЛЬКО intent memory_search (остальные не запускаются), тексты ответа НЕ печатаются; печатаются: чем классифицировано
                (regex|haiku), какой query отдал классификатор, ЧТО реально получил answer(), route, id/type.
Колонки: rank | id | type | cat | score | src
  type: likes|dislikes|important|other — тип факта человека (из key_name);
        'self' — факт без person_id (мои факты); 'fact' — прочее.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

QUERIES = ["что не любит миша", "что любит миша", "что я не ем", "что важно про олю"]

_TYPE_SQL = ("CASE WHEN person_id IS NULL THEN 'self' ELSE "
             "COALESCE(substring(key_name from '^person_[0-9]+_([a-z]+)_[0-9a-f]{8}$'), 'other') END")


def _auto_user(sa, eng):
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT user_id FROM memories WHERE is_current AND NOT is_archived "
            "GROUP BY user_id ORDER BY count(*) DESC LIMIT 1")).first()
    return row[0] if row else ""


def _meta(sa, eng, ids):
    if not ids:
        return {}
    with eng.connect() as c:
        rows = c.execute(sa.text(
            f"SELECT id, category, {_TYPE_SQL} AS t, person_id FROM memories WHERE id = ANY(:i)"),
            {"i": [int(i) for i in ids]}).all()
    return {str(r[0]): (r[1] or "—", r[2], r[3]) for r in rows}


async def run_old(q, user_id):
    from core import memory as mem
    tokens = mem._tokenize_hint(q) or [q]
    ilike = await mem._mem_repo.search(tokens, user_id=user_id, page_size=10)
    merged = await mem._semantic_search_memory(q, ilike, user_id=user_id)
    ilike_ids = {m.id for m in ilike}
    return "old", [(m.id, None, "ilike" if m.id in ilike_ids else "semantic") for m in merged[:5]]


async def run_hybrid(q, user_id):
    from core import memory_search
    hits = await memory_search.hybrid_search(q, user_id=user_id, k=5, do_rerank=True)
    return "hybrid", [(h.memory.id, h.score, f"fts#{h.fts_rank or '-'},vec#{h.vec_rank or '-'}"
                       + ("+rerank" if h.reranked else "")) for h in hits]


async def run_answer(q, user_id):
    from core import memory_search
    res = await memory_search.answer(q, user_id=user_id)
    rows = []
    for g in res.groups:                      # группы по людям (тёзки) либо одна группа
        for h in g.hits:
            rows.append((h.memory.id, getattr(h, "score", None), f"grp={g.person_id or '-'}"))
    head = f"{res.route} kind={res.kind} subject={res.subject} types={','.join(res.types) or '-'}"
    return head, rows


async def run_flow(q, user_id):
    """Реальный путь сообщения. Возвращает (шапка, строки) как другие режимы + печатает диагностику."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from core import classifier, memory_search

    msg = MagicMock()
    msg.text = q
    msg.from_user.id = 1
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    seen = {"answer_queries": [], "results": []}
    real_answer = memory_search.answer
    real_ask = classifier.ask_claude
    haiku_calls = []

    async def spy_answer(query, **kw):
        seen["answer_queries"].append(query)
        res = await real_answer(query, **kw)
        seen["results"].append(res)
        return res

    async def spy_ask(*a, **kw):
        haiku_calls.append(1)
        return await real_ask(*a, **kw)

    items = []
    with patch.object(classifier, "ask_claude", spy_ask), \
         patch("core.memory._memory_search.answer", spy_answer), \
         patch("nexus.handlers.utils.react", AsyncMock()):
        items = await classifier.classify(q, tz_offset=3, user_id=user_id)
        for data in items:
            # READ-ONLY: обрабатываем ТОЛЬКО поиск по памяти. Любой другой intent (задача, расход, заметка…)
            # process_item выполнил бы по-настоящему, поэтому здесь он лишь печатается.
            if data.get("type") == "memory_search":
                await classifier.process_item(data, q, msg, {}, user_id=user_id)

    path = "haiku" if haiku_calls else "regex"
    types = ",".join(i.get("type", "?") for i in items) or "-"
    got = seen["answer_queries"]
    skipped = [i.get("type") for i in items if i.get("type") != "memory_search"]
    verdict = ("NOT-EXECUTED(non-memory intent: %s)" % ",".join(skipped)) if skipped and not got else "-" if not got else ("ORIGINAL" if got == [q] else f"NOT-ORIGINAL got={got!r}")
    sent = "\n".join(str(c.args[0]) for c in msg.answer.await_args_list if c.args)
    old_header = "зап.)" in sent
    head = (f"classify={path} items={types} query_from_classifier={[i.get('query') for i in items]!r} "
            f"answer_got={verdict} old_group_header={old_header}")
    rows = []
    if seen["results"]:
        res = seen["results"][0]
        head += f" | route={res.route} kind={res.kind} subject={res.subject} types={','.join(res.types) or '-'}"
        for g in res.groups:
            for h in g.hits:
                rows.append((h.memory.id, None, f"grp={g.person_id or '-'}"))
    return head, rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["old", "hybrid", "answer", "flow"], required=True)
    ap.add_argument("--user-id", default="")
    ap.add_argument("--query", action="append")
    args = ap.parse_args()

    import sqlalchemy as sa
    from core.db import get_engine
    eng = get_engine()
    user_id = args.user_id or _auto_user(sa, eng)
    runner = {"old": run_old, "hybrid": run_hybrid, "answer": run_answer, "flow": run_flow}[args.mode]

    for q in args.query or QUERIES:
        print(f"\n=== [{args.mode}] {q!r} ===")
        head, rows = await runner(q, user_id)
        print(f"  route: {head}")
        meta = _meta(sa, eng, [r[0] for r in rows])
        if not rows:
            print("  (пусто)")
        for i, (mid, score, src) in enumerate(rows, 1):
            cat, typ, _pid = meta.get(str(mid), ("—", "?", None))
            sc = "-" if score is None else f"{score:.4f}"
            print(f"  {i}. id={mid} type={typ} cat={cat} score={sc} src={src}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
