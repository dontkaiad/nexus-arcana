"""scripts/retype_person_facts.py — детерминированный пересчёт типа фактов людей по тексту (без LLM).

Типы фактов людей поставил Haiku при импорте и они бывают неверны («не любит X» → likes). Здесь тип считается
лексиконом из core.query_parse (`fact_type_from_text`, с учётом отрицания). Где лексикон сработал — тип из
текста перекрывает сохранённый и ключ `person_<id>_<type>_<hash8>` переписывается; где не сработал или факт
двусмыслен — тип НЕ трогаем. Затрагиваются только живые факты (confirmed/pending, текст не пуст, не архив):
отклонённые и стёртые (tombstone) не меняются — их ключ защищает от повторного импорта.

    # dry-run (по умолчанию): только числа — матрица было→стало, конфликты
    docker exec -i nexus-bot python - < scripts/retype_person_facts.py
    # запись (только по явному ok владельца)
    docker exec -i nexus-bot python - --apply < scripts/retype_person_facts.py

Печатает ТОЛЬКО числа (без текстов фактов). Перед записью сохраняет (id, key_name) в `memories_key_bak_retype`;
откат: UPDATE memories m SET key_name = b.key_name FROM memories_key_bak_retype b WHERE b.id = m.id.
Эмбеддинг от типа не зависит — пересчёт не нужен.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import sqlalchemy as sa
    from core.db import get_engine
    from core.people import retype_key
    from core.query_parse import fact_type_from_text
    from core.repos.pg_people_repo import fact_type

    eng = get_engine()
    with eng.connect() as c:
        rows = c.execute(sa.text(
            "SELECT id, user_id, key_name, fact_text FROM memories "
            "WHERE person_id IS NOT NULL AND fact_text <> '' AND NOT is_archived "
            "AND review_status IN ('confirmed','pending') AND key_name ~ '^person_[0-9]+_[a-z]+_[0-9a-f]{8}$' "
            "ORDER BY id")).all()
        live_keys = {(r.user_id, r.key_name) for r in c.execute(sa.text(
            "SELECT user_id, key_name FROM memories WHERE NOT is_archived AND key_name LIKE 'person\\_%'"))}

    matrix: Counter = Counter()
    unchanged = 0
    changes = []
    conflicts = 0
    for r in rows:
        old = fact_type(r.key_name)
        new = fact_type_from_text(r.fact_text)
        if not new or new == old:
            unchanged += 1
            continue
        new_key = retype_key(r.key_name, new)
        if new_key is None:
            continue
        if (r.user_id, new_key) in live_keys:
            conflicts += 1
            continue
        live_keys.add((r.user_id, new_key))
        matrix[(old, new)] += 1
        changes.append((r.id, new_key))

    print("=" * 56)
    print(f"  живых фактов людей:          {len(rows)}")
    print(f"  тип не меняется:             {unchanged}")
    print(f"  сменят тип:                  {len(changes)}")
    print(f"  пропуск (ключ уже занят):    {conflicts}")
    print("  было → стало:")
    for (old, new), n in sorted(matrix.items(), key=lambda x: (-x[1], x[0])):
        print(f"    {old:10} → {new:10} {n}")
    print("=" * 56)

    if not args.apply:
        print("dry-run: ничего не записано (--apply для записи)")
        return 0
    if not changes:
        print("нечего менять")
        return 0
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE IF EXISTS memories_key_bak_retype"))
        c.execute(sa.text("CREATE TABLE memories_key_bak_retype AS "
                          "SELECT id, key_name FROM memories WHERE id = ANY(:i)"), {"i": [i for i, _ in changes]})
        for i, k in changes:
            c.execute(sa.text("UPDATE memories SET key_name = :k, updated_at = now() WHERE id = :i"), {"k": k, "i": i})
    print(f"APPLIED: {len(changes)} (бэкап ключей: memories_key_bak_retype)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
