"""scripts/rewrite_self_gender.py — переписать тексты фактов «Я» из м.р. в ж.р. (Haiku, ключ ТОЛЬКО tools).

    docker exec -i nexus-bot python - --plan  < scripts/rewrite_self_gender.py    # dry-run: плана и выборка, в базу не пишет
    docker exec -i nexus-bot python - --apply < scripts/rewrite_self_gender.py    # только после ок: бэкап memories_text_bak

--plan шлёт ВСЕ тексты «Я» в Haiku пачками (≈ 1600 коротких строк, центы), принимает только правки окончаний (core.self_gender.validate),
остальное кладёт в «подозрительные». Файлы: /tmp/gdplan.json, /tmp/gdsample.md (тексты — только локально, вне git).
--apply: fact_text, key_name (хэш текста) пересчитываются, embedding сбрасывается (потом migrate_memory_embeddings.py --apply);
откат: UPDATE memories m SET fact_text = b.fact_text, key_name = b.key_name, embedding = b.embedding FROM memories_text_bak b WHERE b.id = m.id.
"""
from __future__ import annotations

import os as _os

_os.environ.setdefault("LLM_KEY_SCOPE", "tools")   # LLM только через ANTHROPIC_API_KEY_TOOLS, без отката на ключ бота

import argparse
import asyncio
import json
import sys
from collections import Counter

PLAN = "/tmp/gdplan.json"
SAMPLE = "/tmp/gdsample.md"
BATCH = 60
PRICE_IN, PRICE_OUT = 0.10, 0.50          # Haiku 5.5, $/1M токенов


def _rows(c, sa):
    me = c.execute(sa.text("SELECT id, user_id FROM people WHERE is_self")).one()
    rows = c.execute(sa.text(
        "SELECT id, fact_text, key_name FROM memories WHERE person_id = :p AND NOT is_archived AND review_status <> 'rejected' "
        "AND fact_text <> '' ORDER BY id"), {"p": me.id}).all()
    return me, rows


async def _llm(rows, usage):
    from core.claude_client import ask_claude
    from core.self_gender import SYSTEM, build_prompt, parse_reply
    out = {}
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        u: dict = {}
        raw = await ask_claude(build_prompt([(r.id, r.fact_text) for r in part]), system=SYSTEM, role="categorize",
                               max_tokens=3000, temperature=0, usage_out=u, label="gender_rewrite:rewrite_self_gender")
        usage["in"] += u.get("input_tokens", 0)
        usage["out"] += u.get("output_tokens", 0)
        out.update(parse_reply(raw))
    return out


def plan() -> int:
    import sqlalchemy as sa
    from core.db import get_engine
    from core.self_gender import validate
    with get_engine().connect() as c:
        me, rows = _rows(c, sa)
    usage = {"in": 0, "out": 0}
    got = asyncio.run(_llm(rows, usage))
    by = {r.id: r for r in rows}
    changes, bad = [], []
    for i, new in sorted(got.items()):
        r = by.get(i)
        if r is None:
            continue
        ok, why = validate(r.fact_text, new)
        (changes if ok else bad).append({"id": i, "old": r.fact_text, "new": new, "why": why})
    json.dump({"self_id": me.id, "changes": changes, "suspicious": bad}, open(PLAN, "w"), ensure_ascii=False)
    cost = usage["in"] * PRICE_IN / 1e6 + usage["out"] * PRICE_OUT / 1e6
    lines = [f"# Перевод фактов «Я» м.р. → ж.р.: выборка (ЛОКАЛЬНО, не в git)\n",
             f"Фактов просмотрено: {len(rows)}; правок принято: {len(changes)}; подозрительных (не пишутся): {len(bad)}\n",
             "## Принятые правки: было → стало\n"]
    lines += [f"- #{x['id']}  {x['old']}  →  {x['new']}" for x in changes]
    lines += ["\n## Подозрительные (НЕ применяются)\n"] + [f"- #{x['id']} [{x['why']}]  {x['old']}  →  {x['new']}" for x in bad]
    open(SAMPLE, "w").write("\n".join(lines))
    print(f"просмотрено {len(rows)} | правок {len(changes)} | подозрительных {len(bad)}")
    print(f"Haiku: вход {usage['in']} ток, выход {usage['out']} ток → ${cost:.4f}")
    print("причины подозрительных:", dict(Counter(x["why"] for x in bad)))
    print(f"файлы: {PLAN} {SAMPLE}")
    return 0


def apply() -> int:
    import sqlalchemy as sa
    from core.db import get_engine
    from core.people import fact_hash
    from core.repos.pg_people_repo import fact_type
    data = json.load(open(PLAN))
    done = Counter()
    with get_engine().begin() as c:
        me = c.execute(sa.text("SELECT id FROM people WHERE is_self")).scalar()
        if me != data["self_id"]:
            sys.exit("план составлен для другой карточки «Я»")
        c.execute(sa.text("CREATE TABLE IF NOT EXISTS memories_text_bak AS SELECT id, fact_text, key_name, embedding FROM memories WHERE false"))
        live = {r[0] for r in c.execute(sa.text("SELECT key_name FROM memories WHERE person_id = :p AND NOT is_archived"), {"p": me})}
        for ch in data["changes"]:
            row = c.execute(sa.text("SELECT fact_text, key_name FROM memories WHERE id = :i AND person_id = :p AND NOT is_archived"),
                            {"i": ch["id"], "p": me}).first()
            if not row or row.fact_text != ch["old"]:
                done["skipped_changed"] += 1
                continue
            key = row.key_name
            if key.startswith("person_%d_" % me):
                key = "person_%d_%s_%s" % (me, fact_type(row.key_name), fact_hash(ch["new"]))
                if key != row.key_name and key in live:
                    done["key_conflict"] += 1
                    continue
            c.execute(sa.text("INSERT INTO memories_text_bak SELECT id, fact_text, key_name, embedding FROM memories WHERE id = :i "
                              "AND id NOT IN (SELECT id FROM memories_text_bak)"), {"i": ch["id"]})
            c.execute(sa.text("UPDATE memories SET fact_text = :t, key_name = :k, embedding = NULL, updated_at = now() WHERE id = :i"),
                      {"t": ch["new"], "k": key, "i": ch["id"]})
            live.discard(row.key_name)
            live.add(key)
            done["rewritten"] += 1
    print(dict(done))
    print("дальше: python3 scripts/migrate_memory_embeddings.py --apply  (эмбеддинги переписанных)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sys.exit(apply() if a.apply else plan())
