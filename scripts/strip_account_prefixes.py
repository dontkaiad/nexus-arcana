"""scripts/strip_account_prefixes.py — срезать у фактов человека префикс «Имя: » = display name его аккаунта.

При импорте факты подписывались именем, под которым аккаунт виден в чате («Черепашка: любит чай»). Это не
имя и не алиас карточки, поэтому `strip_name_prefix` их не снимал. Теперь у аккаунта есть `display_name`
(person_accounts.display_name), он входит в related_to и учитывается при срезании.

    # dry-run: кандидаты — повторяющийся префикс у фактов одного человека, не совпадающий с его именами;
    # для каждого — какой аккаунт подтверждён чатом (source_chat_id = external_id аккаунта)
    docker exec -i nexus-bot python - < scripts/strip_account_prefixes.py

    # запись — только для явно названных пар «id человека = префикс» (по ok владельца)
    docker exec -i nexus-bot python - --set 142=Имя1 --set 264=Имя2 --apply < scripts/strip_account_prefixes.py

--apply: проставляет display_name аккаунту (тому, чей external_id = source_chat_id этих фактов; без однозначного
аккаунта — человек пропускается), обновляет related_to, срезает префикс в fact_text, обнуляет embedding у
изменённых и сразу пересчитывает его (Voyage). Бэкап (id, fact_text) → `memories_prefix_bak`.
Откат: UPDATE memories m SET fact_text = b.fact_text, embedding = NULL FROM memories_prefix_bak b WHERE b.id = m.id
(эмбеддинги после отката — пересчитать scripts/migrate_memory_embeddings.py --all).
"""
from __future__ import annotations

import argparse
import re
import sys

PROTECTED = {"важно", "внимание", "аллергия", "примечание", "ps", "note", "кстати", "итог"}
PREFIX = re.compile(r"^\s*([^\s:]{1,40}):\s+(.+)$", re.S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="ID=ПРЕФИКС")
    ap.add_argument("--min-facts", type=int, default=2)
    args = ap.parse_args()

    import sqlalchemy as sa
    from core.db import get_engine
    from core.people import _norm, _tokens
    from core.repos import pg_people_repo as prepo

    eng = get_engine()
    with eng.connect() as c:
        rows = c.execute(sa.text(
            "SELECT m.id, m.person_id, m.fact_text, m.source_chat_id, p.name, p.aliases, p.user_id FROM memories m "
            "JOIN people p ON p.id = m.person_id WHERE m.fact_text <> '' AND NOT m.is_archived AND NOT p.is_self "
            "ORDER BY m.id")).all()
        accs = c.execute(sa.text("SELECT id, person_id, external_id, display_name FROM person_accounts "
                                 "WHERE platform = 'tg'")).all()

    by_person: dict = {}
    for r in rows:
        m = PREFIX.match(r.fact_text)
        if not m:
            continue
        w = m.group(1)
        if _norm(w) in PROTECTED:
            continue
        known = set(_tokens(r.name))
        for a in r.aliases or []:
            known |= set(_tokens(a))
        for a in accs:
            if a.person_id == r.person_id and a.display_name:
                known |= set(_tokens(a.display_name))
        if _norm(w) in known:
            continue
        by_person.setdefault((r.person_id, w), []).append(r)

    def acc_for(pid, facts):
        chats = {f.source_chat_id for f in facts if f.source_chat_id}
        ok = [a for a in accs if a.person_id == pid and a.external_id in chats]
        return ok[0] if len(ok) == 1 and len(chats) == 1 else None

    print("=" * 60)
    print("  КАНДИДАТЫ (префикс повторяется, но это не имя/алиас/display name карточки):")
    for (pid, w), facts in sorted(by_person.items()):
        if len(facts) < args.min_facts:
            continue
        a = acc_for(pid, facts)
        print(f"   person={pid} prefix={w!r} facts={len(facts)} "
              f"account={'id=%s (чат совпал по source_chat_id)' % a.id if a else 'не определён однозначно'}")
    print("=" * 60)

    if not args.apply:
        print("dry-run: ничего не записано")
        return 0
    if not args.set:
        print("--apply без --set ID=ПРЕФИКС: ничего не делаю")
        return 1

    from core.memory_rag import _embed_text, index_memories_batch
    todo = []
    for item in args.set:
        pid_s, _, w = item.partition("=")
        pid = int(pid_s)
        facts = by_person.get((pid, w))
        if not facts:
            print(f"person={pid}: факты с префиксом {w!r} не найдены — пропуск")
            continue
        a = acc_for(pid, facts)
        if not a:
            print(f"person={pid}: аккаунт однозначно не определён — пропуск")
            continue
        todo.append((pid, w, a, facts, facts[0].user_id))

    changed_ids = []
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE IF NOT EXISTS memories_prefix_bak (id bigint, fact_text text)"))
        for pid, w, a, facts, uid in todo:
            c.execute(sa.text("INSERT INTO memories_prefix_bak SELECT id, fact_text FROM memories WHERE id = ANY(:i)"),
                      {"i": [f.id for f in facts]})
            c.execute(sa.text("UPDATE person_accounts SET display_name = :d WHERE id = :a"), {"d": w, "a": a.id})
            for f in facts:
                new = PREFIX.match(f.fact_text).group(2).strip()
                c.execute(sa.text("UPDATE memories SET fact_text = :t, embedding = NULL, updated_at = now() WHERE id = :i"),
                          {"t": new, "i": f.id})
                changed_ids.append(f.id)
            prepo.refresh_related(c, pid)
            print(f"person={pid}: display_name аккаунта {a.id} = {w!r}; префикс срезан у {len(facts)} фактов")

    if changed_ids:
        with eng.connect() as c:
            fresh = c.execute(sa.text("SELECT id, fact_text, related_to, category, person_id FROM memories "
                                      "WHERE id = ANY(:i)"), {"i": changed_ids}).all()
        items = [{"memory_id": str(r.id), "embed_text": _embed_text(r.fact_text, r.related_to, r.category, r.person_id)}
                 for r in fresh]
        done = 0
        for i in range(0, len(items), 20):
            done += index_memories_batch(items[i:i + 20])
        print(f"эмбеддинг пересчитан: {done} из {len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
