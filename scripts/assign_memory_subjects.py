"""scripts/assign_memory_subjects.py — разнос Памяти по разделам: старые памяти → «Я»/карточка, факты «Я» → категории.

Раздел выводится из данных (core/memory_subjects.py), поэтому пишем только то, что реально меняет раздел:
  • старые confirmed-памяти «про Кай» → person_id = «Я» (ключ person_<id>_<type>_<hash8>, related_to карточки);
  • старые памяти про ОДНОГО известного человека → его карточка;
  • факты «Я» (все в категории «👥 Люди») → категория: сначала ключевые слова, остаток — Haiku (роль categorize).
Разбор (review_status) не трогается никогда. «Разное», «Коты», бюджет — без записи.

Фильтр рода (Кай пишет о себе в женском роде): мужской маркер у субъекта (глагол прош. вр. м.р., «он») — факт мог
быть написан не про Кай. Женский маркер в том же факте (глагол на -ла/-лась, «готова», «она/её/ей») перекрывает мужской:
это Кай, не трогаем (отдельный список в выборке). Если в том же чате (source_chat_id) факты ровно ОДНОГО не отклонённого
человека — факт переезжает на его карточку; иначе остаётся pending в «Я» с категорией «❓ Проверить автора» (владелец
отклоняет пачкой сама). «Собеседник» в тексте — rejected (смысл перевёрнут, перевешивать нельзя).
Временные состояния («сейчас находится»…) → rejected, кроме id из --keep-temp.
Ручные правки старых памятей: --manual "140:delete,3:misc,25:self,222:person:Имя,217:birthday" (birthday — ДР в
people.birthday карточки, если она однозначна и ДР там пуст/совпадает; остаток текста факта сохраняется).

Запуск (внутри контейнера; скрипт идёт со stdin):

    # 1. dry-run: считает план, Haiku для остатка (≈ $0.004), пишет файлы; в БД НЕ пишет. Печатает только числа.
    docker exec -i nexus-bot python - --plan [--manual …] < scripts/assign_memory_subjects.py
        → /tmp/msplan.json (id→категория, без текстов) и /tmp/mssample.md (С ТЕКСТАМИ: 25 случайных на категорию +
          все старые памяти; хранить только локально в data/evals/, в git не попадает)
    # 2. запись — ТОЛЬКО по явному «go» владельца, тем же планом, который она смотрела:
    docker cp data/evals/msplan.json nexus-bot:/tmp/msplan.json
    docker exec -i nexus-bot python - --apply < scripts/assign_memory_subjects.py

Перед записью: бэкап `memories_subject_bak(id, category, person_id, key_name, related_to, review_status, embedding)`; удалённые строки — целиком в `memories_deleted_bak`.
Откат: UPDATE memories m SET category=b.category, person_id=b.person_id, key_name=b.key_name,
related_to=b.related_to, review_status=b.review_status, embedding=b.embedding FROM memories_subject_bak b WHERE b.id=m.id.
Для переехавших старых памятей embedding обнуляется (текст эмбеддинга зависит от человека) —
после apply: python3 scripts/migrate_memory_embeddings.py --apply.
Каждая запись защищена условием «строка всё ещё в том состоянии, что в плане» (иначе пропуск и счётчик).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
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
        "SELECT id, fact_text, source_chat_id FROM memories WHERE person_id = :p AND category = '👥 Люди' "
        "AND review_status = 'pending' AND fact_text <> '' AND NOT is_archived ORDER BY id"), {"p": me.id}).all()
    old = c.execute(sa.text(
        "SELECT id, category, key_name, fact_text, related_to, user_id FROM memories WHERE person_id IS NULL "
        "AND review_status = 'confirmed' AND NOT is_archived AND is_current ORDER BY id")).all()
    return me, selfs, old


def _parse_manual(arg):
    out = {}
    for part in (arg or "").split(","):
        bits = [b.strip() for b in part.split(":") if b.strip()]
        if len(bits) >= 2 and bits[0].isdigit() and bits[1] in ("delete", "misc", "self", "person", "birthday"):
            out[int(bits[0])] = (bits[1], bits[2] if len(bits) > 2 else None)
    return out


_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_BD_RE = re.compile(r"[,;—\-]?\s*(?:день рождения|др)\s*:?\s*(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?(?:\s*(?:года|г\.?))?", re.I)


def _parse_birthday(text):
    """«… день рождения 18 мая 1994 года» → (month, day, year|None, остаток текста без этой части) или None."""
    m = _BD_RE.search(text or "")
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    rest = (text[:m.start()] + text[m.end():]).strip(" ,;—-")
    return _MONTHS[m.group(2).lower()], int(m.group(1)), int(m.group(3)) if m.group(3) else None, rest


def _chat_people(c, sa, chats, me_id):
    """source_chat_id → {person_id: имя}: не отклонённые люди, у которых есть факты (любого статуса) из того же чата.
    Фильтр «pending» не годится: факты других людей из этих чатов уже разобраны (confirmed/rejected)."""
    rows = c.execute(sa.text(
        "SELECT m.source_chat_id AS chat, p.id AS pid, p.name FROM memories m JOIN people p ON p.id = m.person_id "
        "WHERE m.source_chat_id = ANY(:c) AND NOT m.is_archived "
        "AND NOT p.is_self AND p.status <> 'rejected' GROUP BY 1, 2, 3"), {"c": list(chats)}).all()
    out: dict = {}
    for r in rows:
        out.setdefault(r.chat, {})[r.pid] = r.name
    return out


async def _haiku(rows, usage):
    from core.claude_client import ask_claude
    from core.memory_subjects import CATEGORIZE_SYSTEM, parse_categorize_reply
    out = {}
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        prompt = "\n".join(f"{r.id}|{(r.fact_text or '').replace(chr(10), ' ')[:300]}" for r in part)
        u: dict = {}
        raw = await ask_claude(prompt, system=CATEGORIZE_SYSTEM, role="categorize", max_tokens=1500, temperature=0,
                               usage_out=u, label="categorize:assign_memory_subjects")
        usage["in"] += u.get("input_tokens", 0)
        usage["out"] += u.get("output_tokens", 0)
        out.update(parse_categorize_reply(raw, [r.id for r in part]))
    return out


def plan(manual_arg: str = "", keep_temp: str = "") -> int:
    import sqlalchemy as sa
    from core.db import get_engine
    from core.memory_subjects import keyword_category, legacy_target, gender_verdict, temp_signal, load_cat_names
    from core.repos import pg_people_repo as prepo

    manual = _parse_manual(manual_arg)
    keep = {int(x) for x in re.findall(r"\d+", keep_temp or "")}
    with get_engine().connect() as c:
        me, selfs, old = _load(c, sa)
        verdicts = {r.id: gender_verdict(r.fact_text) for r in selfs}
        chats = {r.source_chat_id for r in selfs if verdicts[r.id][0] == "male" and r.source_chat_id}
        by_chat = _chat_people(c, sa, chats, me.id) if chats else {}
    gender, overridden = [], []
    for r in selfs:
        v, sig = verdicts[r.id]
        if v == "kept":
            overridden.append({"id": r.id, "signals": sig})
        elif v == "interloc":
            gender.append({"id": r.id, "action": "reject", "why": "«Собеседник»: смысл перевёрнут", "signals": sig})
        elif v == "male":
            cand = by_chat.get(r.source_chat_id, {})
            if len(cand) == 1:
                pid, pname = next(iter(cand.items()))
                gender.append({"id": r.id, "action": "reassign", "person_id": pid, "person": pname, "signals": sig})
            else:
                gender.append({"id": r.id, "action": "check", "signals": sig,
                               "why": "несколько людей в чате" if cand else "чат не сопоставлен"})
    gender_ids = {g["id"] for g in gender}

    rest_rows = [r for r in selfs if r.id not in gender_ids]
    temp_all = [r.id for r in rest_rows if temp_signal(r.fact_text)]
    temp_ids = [i for i in temp_all if i not in keep]          # применяются; keep — остаются в категориях
    temp_set = set(temp_ids)
    cat_rows = [r for r in rest_rows if r.id not in temp_set]
    kw, rest = {}, []
    cat_names = load_cat_names(me.user_id)                      # имена и клички котов владельца — из таблицы cats
    for r in cat_rows:
        cat = keyword_category(r.fact_text, cat_names)
        if cat:
            kw[r.id] = cat
        else:
            rest.append(r)
    usage = {"in": 0, "out": 0}
    hk = asyncio.run(_haiku(rest, usage)) if rest else {}
    self_plan = [{"id": r.id, "cat": kw[r.id], "by": "kw"} for r in cat_rows if r.id in kw] + \
                [{"id": r.id, "cat": hk[r.id], "by": "haiku"} for r in rest]
    self_plan.sort(key=lambda x: x["id"])

    legacy_plan, notes = [], []
    for r in old:
        first = (r.related_to or "").split("|")[0].split(",")[0].strip()
        pids = [p["id"] for p in prepo.resolve(r.user_id, [first])] if first else []
        to = legacy_target(r.category, r.key_name, pids)
        item = {"id": r.id, "to": to, "person_id": pids[0] if to == "person" else None}
        if r.id in manual:
            act, arg = manual[r.id]
            item["manual"] = True
            if act == "person":
                found = prepo.resolve(r.user_id, [arg]) if arg else []
                if len(found) == 1:
                    item.update(to="person", person_id=found[0]["id"])
                else:
                    item.update(to="misc", person_id=None)
                    notes.append(f"#{r.id}: карточка «{arg}» не найдена однозначно ({len(found)}) — оставлено как есть")
            elif act == "birthday":
                bd = _parse_birthday(r.fact_text)
                if len(set(pids)) == 1 and bd:
                    item.update(to="birthday", person_id=pids[0], month=bd[0], day=bd[1], year=bd[2], rest=bd[3])
                else:
                    item.update(to="misc", person_id=None)
                    notes.append(f"#{r.id}: ДР не перенесён — карточка {'неоднозначна' if len(set(pids)) != 1 else 'однозначна'}, "
                                 f"дата {'не разобрана' if not bd else 'разобрана'} — оставлено как есть")
            else:
                item.update(to=act, person_id=None)
        legacy_plan.append(item)
    for i in manual:
        if i not in {o.id for o in old}:
            notes.append(f"#{i}: нет среди активных старых памятей — ручная правка пропущена")

    json.dump({"self_id": me.id, "self": self_plan, "gender": gender, "overridden": overridden, "temp": temp_ids,
               "legacy": legacy_plan}, open(PLAN, "w"), ensure_ascii=False)

    texts = {r.id: r.fact_text for r in selfs}
    by_cat: dict = {}
    for it in self_plan:
        by_cat.setdefault(it["cat"], []).append(it)
    rnd = random.Random(7)
    olds = {r.id: r for r in old}
    with open(SAMPLE, "w") as f:
        f.write("# Разнос Памяти v3: выборка для проверки глазами (ЛОКАЛЬНО, не в git)\n\n")
        f.write(f"## 1. Фильтр рода: {len(gender)} фактов «Я» с мужскими маркерами\n\n")
        f.write("Формат: `#id [маркеры] текст` → куда\n\n")
        titles = (("reassign", "Перевешено на человека из того же чата"),
                  ("check", "Остаются pending в «Я», категория «❓ Проверить автора»"),
                  ("reject", "Отклонено (rejected): «Собеседник»"))
        for act, title in titles:
            items = [g for g in gender if g["action"] == act]
            f.write(f"### {title}: {len(items)}\n")
            for g in items:
                to = {"reassign": f"→ {g.get('person')} (карточка {g.get('person_id')})",
                      "check": f"→ ❓ ({g.get('why')})", "reject": "→ rejected"}[act]
                f.write(f"- #{g['id']} [{', '.join(g['signals'])}] {texts[g['id']]}  {to}\n")
            f.write("\n")
        f.write(f"## 1б. Женский маркер перекрыл мужской — остаются «Я», не трогаем: {len(overridden)}\n\n")
        for o in overridden:
            f.write(f"- #{o['id']} [{' '.join(o['signals'])}] {texts[o['id']]}\n")
        f.write(f"\n## 2. Временные состояния → rejected: {len(temp_ids)} (оставлены по твоему списку: "
                f"{len([i for i in temp_all if i in keep])})\n\n")
        for i in temp_all:
            f.write(f"- #{i} {'[ОСТАЮТСЯ]' if i in keep else '[→ rejected]'} {texts[i]}\n")
        f.write("\n## 3. Факты «Я» → категория: до 25 случайных на категорию (без фильтра рода и временных)\n\n")
        f.write("Формат: `#id [kw|haiku] текст`\n\n")
        for cat in sorted(by_cat):
            items = by_cat[cat]
            pick = rnd.sample(items, min(PER_CAT, len(items)))
            f.write(f"### {cat}  (всего {len(items)}, показано {len(pick)})\n")
            for it in sorted(pick, key=lambda x: x["id"]):
                f.write(f"- #{it['id']} [{it['by']}] {texts[it['id']]}\n")
            f.write("\n")
        f.write("## 4. Старые памяти без человека (все активные, кроме бюджета)\n\n")
        label = {"self": "→ Я", "person": "→ карточка", "cats": "→ Коты", "misc": "→ Разное", "delete": "→ УДАЛИТЬ",
                 "birthday": "→ ДР в карточку"}
        for it in legacy_plan:
            if it["to"] == "system":
                continue
            r = olds[it["id"]]
            extra = ""
            if it["to"] == "birthday":
                extra = f" [{it['day']:02d}.{it['month']:02d}.{it['year'] or '----'}; остаток факта: «{it['rest'] or '—'}»]"
            f.write(f"- #{r.id} {label[it['to']]}{extra}{' (вручную)' if it.get('manual') else ''} (было: {r.category}) {r.fact_text}\n")
        for n in notes:
            f.write(f"\n> {n}\n")

    print("факты «Я» (pending):", len(selfs))
    print("  фильтр рода:", len(gender), dict(Counter(g["action"] for g in gender)),
          "| женский маркер перекрыл мужской:", len(overridden))
    print("  временные → rejected:", len(temp_ids), "| оставлены:", len([i for i in temp_all if i in keep]))
    print("  остаток в категории:", len(self_plan), "| ключевые слова:", len(kw), "| Haiku:", len(rest))
    for cat, n in Counter(i["cat"] for i in self_plan).most_common():
        print(f"    {n:5d}  {cat}  (kw {sum(1 for i in by_cat[cat] if i['by']=='kw')}, haiku {sum(1 for i in by_cat[cat] if i['by']=='haiku')})")
    print("старые памяти:", len(old), dict(Counter(i["to"] for i in legacy_plan)))
    for n in notes:
        print("  !", n)
    cost = usage["in"] / 1e6 * PRICE_IN + usage["out"] / 1e6 * PRICE_OUT
    print(f"Haiku: вход {usage['in']} ток, выход {usage['out']} ток → ${cost:.4f}")
    print("файлы:", PLAN, SAMPLE, "(скопировать docker cp; тексты — только локально)")
    return 0


def apply() -> int:
    import sqlalchemy as sa
    from core.db import get_engine
    from core.memory_subjects import REVIEW_AUTHOR, SELF_CATEGORIES
    from core.people import UNKNOWN_YEAR, fact_hash, guess_fact_type, related_to_str

    data = json.load(open(PLAN))
    for it in data["self"]:
        if it["cat"] not in SELF_CATEGORIES:
            sys.exit(f"неизвестная категория в плане: {it['cat']!r}")
    eng = get_engine()
    with eng.begin() as c:
        me = c.execute(sa.text("SELECT id, user_id, name, aliases FROM people WHERE is_self")).one()
        if me.id != data["self_id"]:
            sys.exit("план составлен для другой карточки «Я» — составь заново")
        c.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS memories_subject_bak AS SELECT id, category, person_id, key_name, related_to, "
            "review_status, fact_text, embedding FROM memories WHERE false"))
        c.execute(sa.text("CREATE TABLE IF NOT EXISTS memories_deleted_bak AS SELECT * FROM memories WHERE false"))
        ids = ([i["id"] for i in data["self"]] + [g["id"] for g in data.get("gender", [])] + list(data.get("temp", []))
               + [i["id"] for i in data["legacy"] if i["to"] in ("self", "person", "birthday")])
        c.execute(sa.text(
            "INSERT INTO memories_subject_bak SELECT id, category, person_id, key_name, related_to, review_status, "
            "fact_text, embedding FROM memories WHERE id = ANY(:i) AND id NOT IN (SELECT id FROM memories_subject_bak)"),
            {"i": ids})
        dels = [i["id"] for i in data["legacy"] if i["to"] == "delete"]
        if dels:
            c.execute(sa.text(
                "INSERT INTO memories_deleted_bak SELECT * FROM memories WHERE id = ANY(:i) AND person_id IS NULL "
                "AND id NOT IN (SELECT id FROM memories_deleted_bak)"), {"i": dels})

        live = {(r.user_id, r.key_name) for r in c.execute(sa.text(
            "SELECT user_id, key_name FROM memories WHERE NOT is_archived AND key_name LIKE 'person\\_%'"))}

        def move(row_id, row_text, user_id, pid, extra_where, params, new_text=None):
            p = c.execute(sa.text("SELECT id, name, aliases FROM people WHERE id = :p AND user_id = :u"),
                          {"p": pid, "u": user_id}).first()
            if not p:
                return "skipped"
            text_ = new_text if new_text is not None else row_text
            key = "person_%d_%s_%s" % (p.id, guess_fact_type(text_), fact_hash(text_))
            if (user_id, key) in live:
                return "key_conflict"
            n = c.execute(sa.text(
                "UPDATE memories SET person_id = :p, key_name = :k, related_to = :r, embedding = NULL, "
                f"fact_text = :t WHERE id = :id AND {extra_where}"),
                {**params, "p": p.id, "k": key, "r": related_to_str(p.name, p.aliases or []), "t": text_, "id": row_id}).rowcount
            if n:
                live.add((user_id, key))
                return "moved"
            return "skipped"

        done = Counter()
        handled = set()
        for g in data.get("gender", []):
            handled.add(g["id"])
            row = c.execute(sa.text(
                "SELECT fact_text FROM memories WHERE id = :id AND person_id = :me AND review_status = 'pending' "
                "AND category = '👥 Люди'"), {"id": g["id"], "me": me.id}).first()
            if not row:
                done["gender_skipped"] += 1
                continue
            if g["action"] == "reassign":
                r = move(g["id"], row.fact_text, me.user_id, g["person_id"], "person_id = :me AND review_status = 'pending'",
                         {"me": me.id})
                done["gender_" + ("reassigned" if r == "moved" else r)] += 1
            elif g["action"] == "check":
                c.execute(sa.text("UPDATE memories SET category = :cat WHERE id = :id"), {"cat": REVIEW_AUTHOR, "id": g["id"]})
                done["gender_to_check"] += 1
            else:
                c.execute(sa.text("UPDATE memories SET review_status = 'rejected' WHERE id = :id"), {"id": g["id"]})
                done["gender_rejected"] += 1

        for i in data.get("temp", []):
            handled.add(i)
            n = c.execute(sa.text(
                "UPDATE memories SET review_status = 'rejected' WHERE id = :id AND person_id = :me "
                "AND review_status = 'pending' AND category = '👥 Люди'"), {"id": i, "me": me.id}).rowcount
            done["temp_rejected" if n else "temp_skipped"] += 1

        for it in data["self"]:
            if it["id"] in handled:
                continue
            n = c.execute(sa.text(
                "UPDATE memories SET category = :cat WHERE id = :id AND person_id = :p AND category = '👥 Люди'"),
                {"cat": it["cat"], "id": it["id"], "p": me.id}).rowcount
            done["self_cat" if n else "self_skipped"] += 1

        for it in data["legacy"]:
            if it["to"] == "delete":
                n = c.execute(sa.text("DELETE FROM memories WHERE id = :id AND person_id IS NULL"), {"id": it["id"]}).rowcount
                done["legacy_deleted" if n else "legacy_skipped"] += 1
                continue
            if it["to"] == "birthday":
                row = c.execute(sa.text(
                    "SELECT fact_text, user_id FROM memories WHERE id = :id AND person_id IS NULL "
                    "AND review_status = 'confirmed' AND NOT is_archived"), {"id": it["id"]}).first()
                p = c.execute(sa.text("SELECT id, birthday, birthday_year_known FROM people WHERE id = :p"),
                              {"p": it["person_id"]}).first()
                if not row or not p:
                    done["legacy_skipped"] += 1
                    continue
                year = it.get("year") or UNKNOWN_YEAR
                new_bd = f"{year:04d}-{it['month']:02d}-{it['day']:02d}"
                if p.birthday is not None and (p.birthday.month, p.birthday.day) != (it["month"], it["day"]):
                    done["birthday_conflict"] += 1            # в карточке уже другая дата — не перезаписываем, факт остаётся
                    continue
                if p.birthday is None or (it.get("year") and not p.birthday_year_known):      # пусто, либо уточняем год
                    c.execute(sa.text("UPDATE people SET birthday = :b, birthday_year_known = :k, updated_at = now() WHERE id = :p"),
                              {"b": new_bd, "k": bool(it.get("year")), "p": p.id})
                    done["birthday_set"] += 1
                rest = (it.get("rest") or "").strip()
                if len(re.sub(r"\W+", "", rest)) >= 8:
                    r = move(it["id"], row.fact_text, row.user_id, p.id, "person_id IS NULL", {}, new_text=rest)
                    done["birthday_fact_kept" if r == "moved" else "birthday_fact_" + r] += 1
                else:
                    c.execute(sa.text("INSERT INTO memories_deleted_bak SELECT * FROM memories WHERE id = :id "
                                      "AND id NOT IN (SELECT id FROM memories_deleted_bak)"), {"id": it["id"]})
                    c.execute(sa.text("DELETE FROM memories WHERE id = :id"), {"id": it["id"]})
                    done["birthday_fact_deleted"] += 1
                continue
            if it["to"] not in ("self", "person"):
                continue
            row = c.execute(sa.text(
                "SELECT fact_text, user_id FROM memories WHERE id = :id AND person_id IS NULL "
                "AND review_status = 'confirmed' AND NOT is_archived"), {"id": it["id"]}).first()
            if not row:
                done["legacy_skipped"] += 1
                continue
            pid = me.id if it["to"] == "self" else it["person_id"]
            r = move(it["id"], row.fact_text, row.user_id, pid, "person_id IS NULL AND review_status = 'confirmed'", {})
            done["legacy_to_" + it["to"] if r == "moved" else "legacy_" + r] += 1
    print(dict(done))
    print("дальше: python3 scripts/migrate_memory_embeddings.py --apply  (эмбеддинги переехавших)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--manual", default="")
    ap.add_argument("--keep-temp", default="")
    a = ap.parse_args()
    sys.exit(apply() if a.apply else plan(a.manual, a.keep_temp))
