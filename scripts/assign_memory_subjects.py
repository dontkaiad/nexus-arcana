"""scripts/assign_memory_subjects.py — разнос Памяти по разделам: старые памяти → «Я»/карточка, факты «Я» → категории.

Раздел выводится из данных (core/memory_subjects.py), поэтому пишем только то, что реально меняет раздел:
  • старые confirmed-памяти «про Кай» → person_id = «Я» (ключ person_<id>_<type>_<hash8>, related_to карточки);
  • старые памяти про ОДНОГО известного человека → его карточка;
  • факты «Я» (все в категории «👥 Люди») → категория: сначала ключевые слова, остаток — Haiku (роль categorize).
Разбор (review_status) не трогается никогда. «Разное», «Коты», бюджет — без записи.

Запуск (внутри контейнера; скрипт идёт со stdin):

    # 1. dry-run: считает план, Haiku для остатка (≈ $0.004), пишет файлы; в БД НЕ пишет. Печатает только числа.
    docker exec -i nexus-bot python - --plan < scripts/assign_memory_subjects.py
        → /tmp/msplan.json (id→категория, без текстов) и /tmp/mssample.md (С ТЕКСТАМИ: 25 случайных на категорию +
          все старые памяти; хранить только локально в data/evals/, в git не попадает)
    # 2. запись — ТОЛЬКО по явному «go» владельца, тем же планом, который она смотрела:
    docker cp data/evals/msplan.json nexus-bot:/tmp/msplan.json
    docker exec -i nexus-bot python - --apply < scripts/assign_memory_subjects.py

Перед записью: бэкап `memories_subject_bak(id, category, person_id, key_name, related_to, embedding)`.
Откат: UPDATE memories m SET category=b.category, person_id=b.person_id, key_name=b.key_name,
related_to=b.related_to, embedding=b.embedding FROM memories_subject_bak b WHERE b.id=m.id.
Для переехавших старых памятей embedding обнуляется (текст эмбеддинга зависит от человека) —
после apply: python3 scripts/migrate_memory_embeddings.py --apply.
Каждая запись защищена условием «строка всё ещё в том состоянии, что в плане» (иначе пропуск и счётчик).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter

PLAN = "/tmp/msplan.json"
SAMPLE = "/tmp/mssample.md"
BATCH = 50
PER_CAT = 25
PRICE_IN, PRICE_OUT = 0.10, 0.50          # Haiku 5.5, $ за 1M токенов


def _load(c, sa):
    me = c.execute(sa.text("SELECT id, user_id, name, aliases FROM people WHERE is_self ORDER BY id")).all()
    if len(me) != 1:
        sys.exit(f"ожидалась ровно одна карточка «Я», найдено {len(me)}")
    me = me[0]
    selfs = c.execute(sa.text(
        "SELECT id, fact_text FROM memories WHERE person_id = :p AND category = '👥 Люди' AND fact_text <> '' "
        "AND NOT is_archived ORDER BY id"), {"p": me.id}).all()
    old = c.execute(sa.text(
        "SELECT id, category, key_name, fact_text, related_to, user_id FROM memories WHERE person_id IS NULL "
        "AND review_status = 'confirmed' AND NOT is_archived AND is_current ORDER BY id")).all()
    return me, selfs, old


async def _haiku(rows, usage):
    from core.claude_client import ask_claude
    from core.memory_subjects import CATEGORIZE_SYSTEM, parse_categorize_reply
    out = {}
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        prompt = "\n".join(f"{r.id}|{(r.fact_text or '').replace(chr(10), ' ')[:300]}" for r in part)
        u: dict = {}
        raw = await ask_claude(prompt, system=CATEGORIZE_SYSTEM, role="categorize", max_tokens=1500, temperature=0, usage_out=u,
                               label="categorize:assign_memory_subjects")
        usage["in"] += u.get("input_tokens", 0)
        usage["out"] += u.get("output_tokens", 0)
        out.update(parse_categorize_reply(raw, [r.id for r in part]))
    return out


def plan() -> int:
    import sqlalchemy as sa
    from core.db import get_engine
    from core.memory_subjects import keyword_category, legacy_target
    from core.repos import pg_people_repo as prepo

    with get_engine().connect() as c:
        me, selfs, old = _load(c, sa)

    kw, rest = {}, []
    for r in selfs:
        cat = keyword_category(r.fact_text)
        if cat:
            kw[r.id] = cat
        else:
            rest.append(r)
    usage = {"in": 0, "out": 0}
    hk = asyncio.run(_haiku(rest, usage)) if rest else {}
    self_plan = [{"id": r.id, "cat": kw[r.id], "by": "kw"} for r in selfs if r.id in kw] + \
                [{"id": r.id, "cat": hk[r.id], "by": "haiku"} for r in rest]
    self_plan.sort(key=lambda x: x["id"])

    legacy_plan = []
    for r in old:
        first = (r.related_to or "").split("|")[0].split(",")[0].strip()
        pids = [p["id"] for p in prepo.resolve(r.user_id, [first])] if first else []
        to = legacy_target(r.category, r.key_name, pids)
        legacy_plan.append({"id": r.id, "to": to, "person_id": pids[0] if to == "person" else None})

    json.dump({"self_id": me.id, "self": self_plan, "legacy": legacy_plan}, open(PLAN, "w"), ensure_ascii=False)

    texts = {r.id: r.fact_text for r in selfs}
    by_cat: dict = {}
    for it in self_plan:
        by_cat.setdefault(it["cat"], []).append(it)
    rnd = random.Random(7)
    with open(SAMPLE, "w") as f:
        f.write("# Разнос Памяти: выборка для проверки глазами (ЛОКАЛЬНО, не в git)\n\n")
        f.write("## Факты «Я» → категория: до 25 случайных на категорию\n\nФормат: `#id [kw|haiku] текст`\n\n")
        for cat in sorted(by_cat):
            items = by_cat[cat]
            pick = rnd.sample(items, min(PER_CAT, len(items)))
            f.write(f"### {cat}  (всего {len(items)}, показано {len(pick)})\n")
            for it in sorted(pick, key=lambda x: x["id"]):
                f.write(f"- #{it['id']} [{it['by']}] {texts[it['id']]}\n")
            f.write("\n")
        f.write("## Старые памяти без человека (все активные, кроме бюджета)\n\n")
        olds = {r.id: r for r in old}
        label = {"self": "→ Я", "person": "→ карточка", "cats": "→ Коты", "misc": "→ Разное"}
        for it in legacy_plan:
            if it["to"] == "system":
                continue
            r = olds[it["id"]]
            f.write(f"- #{r.id} {label[it['to']]} (было: {r.category}) {r.fact_text}\n")

    print("факты «Я»:", len(selfs), "| ключевые слова:", len(kw), "| Haiku:", len(rest))
    for cat, n in Counter(i["cat"] for i in self_plan).most_common():
        print(f"  {n:5d}  {cat}  (kw {sum(1 for i in by_cat[cat] if i['by']=='kw')}, haiku {sum(1 for i in by_cat[cat] if i['by']=='haiku')})")
    print("старые памяти:", len(old), dict(Counter(i["to"] for i in legacy_plan)))
    cost = usage["in"] / 1e6 * PRICE_IN + usage["out"] / 1e6 * PRICE_OUT
    print(f"Haiku: вход {usage['in']} ток, выход {usage['out']} ток → ${cost:.4f}")
    print("файлы:", PLAN, SAMPLE, "(скопировать docker cp; тексты — только локально)")
    return 0


def apply() -> int:
    import sqlalchemy as sa
    from core.db import get_engine
    from core.memory_subjects import SELF_CATEGORIES
    from core.people import fact_hash, guess_fact_type, related_to_str
    from core.repos import pg_people_repo as prepo

    data = json.load(open(PLAN))
    eng = get_engine()
    with eng.begin() as c:
        me = c.execute(sa.text("SELECT id, user_id, name, aliases FROM people WHERE is_self")).one()
        if me.id != data["self_id"]:
            sys.exit("план составлен для другой карточки «Я» — составь заново")
        c.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS memories_subject_bak AS SELECT id, category, person_id, key_name, related_to, "
            "embedding FROM memories WHERE false"))
        ids = [i["id"] for i in data["self"]] + [i["id"] for i in data["legacy"] if i["to"] in ("self", "person")]
        c.execute(sa.text(
            "INSERT INTO memories_subject_bak SELECT id, category, person_id, key_name, related_to, embedding "
            "FROM memories WHERE id = ANY(:i) AND id NOT IN (SELECT id FROM memories_subject_bak)"), {"i": ids})

        done = Counter()
        for it in data["self"]:
            if it["cat"] not in SELF_CATEGORIES:
                sys.exit(f"неизвестная категория в плане: {it['cat']!r}")
            n = c.execute(sa.text(
                "UPDATE memories SET category = :cat WHERE id = :id AND person_id = :p AND category = '👥 Люди'"),
                {"cat": it["cat"], "id": it["id"], "p": me.id}).rowcount
            done["self_cat" if n else "self_skipped"] += 1

        live = {(r.user_id, r.key_name) for r in c.execute(sa.text(
            "SELECT user_id, key_name FROM memories WHERE NOT is_archived AND key_name LIKE 'person\\_%'"))}
        for it in data["legacy"]:
            if it["to"] not in ("self", "person"):
                continue
            row = c.execute(sa.text(
                "SELECT id, fact_text, user_id FROM memories WHERE id = :id AND person_id IS NULL "
                "AND review_status = 'confirmed' AND NOT is_archived"), {"id": it["id"]}).first()
            if not row:
                done["legacy_skipped"] += 1
                continue
            pid = me.id if it["to"] == "self" else it["person_id"]
            p = c.execute(sa.text("SELECT id, name, aliases FROM people WHERE id = :p AND user_id = :u"),
                          {"p": pid, "u": row.user_id}).first()
            if not p:
                done["legacy_skipped"] += 1
                continue
            key = "person_%d_%s_%s" % (p.id, guess_fact_type(row.fact_text), fact_hash(row.fact_text))
            if (row.user_id, key) in live:
                done["legacy_key_conflict"] += 1
                continue
            live.add((row.user_id, key))
            c.execute(sa.text(
                "UPDATE memories SET person_id = :p, key_name = :k, related_to = :r, embedding = NULL WHERE id = :id"),
                {"p": p.id, "k": key, "r": related_to_str(p.name, p.aliases or []), "id": row.id})
            done["legacy_to_" + it["to"]] += 1
    print(dict(done))
    print("дальше: python3 scripts/migrate_memory_embeddings.py --apply  (эмбеддинги переехавших)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sys.exit(apply() if a.apply else plan())
