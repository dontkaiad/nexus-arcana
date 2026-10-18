"""staging → People domain (ADR-0027): `people` + pending-факты в 🧠 Памяти.

Всё из staging импортируется (staging-approve убран: проверка — в Mini App).
Идентичность: жёсткие ключи tg_id / vk_id. Автосклейка VK↔TG — только при
совпадении нормализованных имени+фамилии И (одинаковый ДР ИЛИ tg_username ==
vk screen_name); неоднозначное не клеим (останутся отдельными pending-людьми,
склеит merge). Факты: person_id, review_status=pending, source=auto,
ключ person_{person_id}_{type}_{hash8}. ДР → только people.birthday.

.env: PEOPLE_IMPORT_USER_ID. Запуск:
  python3 -m tools.people_import.import_memory --dry-run
  python3 -m tools.people_import.import_memory [--no-embed] [--batch-size 20] [--sleep 21]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
from collections import Counter
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

import sqlalchemy as sa

from tools.people_import.common import (
    env, fact_hash, load_staging, name_key, parse_birthday,
)

from core.db import get_engine
from core.memory_rag import _embed_text, index_memories_batch
from core.repos import pg_people_repo as prepo
from core.repos.pg_memory_repo import PgMemoryRepo, bot_to_scope

CATEGORY = "👥 Люди"
BOT = "☀️ Nexus"
SOURCE = "auto"
_VK_NOISE = re.compile(r"^id\d+$")


# ── планирование (чистые функции, без БД) ───────────────────────────────────

def _new_cand() -> Dict:
    return {"names": Counter(), "aliases": set(), "tg_id": None, "tg_username": None,
            "vk_id": None, "vk_screen": None, "sources": set(), "facts": [],
            "bday_api": [], "bday_chat": [], "bday_unparsed": 0, "ambiguous": False}


def _absorb(c: Dict, r: Dict) -> None:
    c["names"][r["name"]] += 1
    c["aliases"].update(a for a in (r.get("aliases") or []) if a)
    c["sources"].add(r.get("source") or "")
    for f in ("tg_id", "tg_username", "vk_id", "vk_screen"):
        if r.get(f) and not c[f]:
            c[f] = r[f]
    if r["type"] == "birthday":
        parsed = parse_birthday(r["fact"])
        if parsed is None:
            c["bday_unparsed"] += 1
        else:
            c["bday_api" if r.get("origin") == "api" else "bday_chat"].append(parsed)
    else:
        c["facts"].append((r["type"], r["fact"].strip()))


def _keys_of(c: Dict) -> Set[str]:
    return {name_key(n) for n in list(c["names"]) + list(c["aliases"])}


def _birthday(c: Dict) -> Optional[Tuple[date, bool]]:
    pool = c["bday_api"] or c["bday_chat"]
    if not pool:
        return None
    return sorted(pool, key=lambda x: not x[1])[0]  # дата с известным годом — вперёд


def _primary_name(c: Dict) -> str:
    return max(c["names"], key=lambda n: (len(n.split()), c["names"][n], len(n)))


def _same_bday(a: Optional[Tuple[date, bool]], b: Optional[Tuple[date, bool]]) -> bool:
    if not a or not b:
        return False
    (da, ka), (db, kb) = a, b
    if (da.month, da.day) != (db.month, db.day):
        return False
    return da.year == db.year if (ka and kb) else True


def group_candidates(rows: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """Строки staging → кандидаты-люди. Возвращает (кандидаты, статистика склейки)."""
    stats = {"merged_cross": 0, "ambiguous_idless": 0}
    by_ident: Dict[Tuple[str, int], Dict] = {}
    cands: List[Dict] = []
    idless: List[Dict] = []
    for r in rows:
        ident = ("tg", r["tg_id"]) if r.get("tg_id") else ("vk", r["vk_id"]) if r.get("vk_id") else None
        if ident is None:
            idless.append(r)
            continue
        c = by_ident.get(ident)
        if c is None:
            c = by_ident[ident] = _new_cand()
            cands.append(c)
        _absorb(c, r)
    # без id: привязываем к единственному id-кандидату того же источника с тем же именем
    idless_groups: Dict[Tuple[str, str], Dict] = {}
    for r in idless:
        src, k = r.get("source") or "", name_key(r["name"])
        hits = [c for c in cands if src in c["sources"] and k in _keys_of(c)]
        if len(hits) == 1:
            _absorb(hits[0], r)
            continue
        c = idless_groups.get((src, k))
        if c is None:
            c = idless_groups[(src, k)] = _new_cand()
            c["ambiguous"] = len(hits) > 1
            cands.append(c)
            if c["ambiguous"]:
                stats["ambiguous_idless"] += 1
        _absorb(c, r)

    # автосклейка VK↔TG
    tgs = [c for c in cands if c["sources"] == {"tg"}]
    vks = [c for c in cands if c["sources"] == {"vk"}]

    def matches(t: Dict, v: Dict) -> bool:
        nt, nv = _primary_name(t), _primary_name(v)
        if len(nt.split()) < 2 or name_key(nt) != name_key(nv):
            return False
        user_eq = bool(t["tg_username"] and v["vk_screen"]
                       and t["tg_username"].lstrip("@").lower() == v["vk_screen"].lower())
        return _same_bday(_birthday(t), _birthday(v)) or user_eq

    pairs = {id(t): [v for v in vks if matches(t, v)] for t in tgs}
    back = Counter(id(v) for vs in pairs.values() for v in vs)
    drop = set()
    for t in tgs:
        vs = pairs[id(t)]
        if len(vs) == 1 and back[id(vs[0])] == 1:
            v = vs[0]
            t["names"].update(v["names"])
            t["aliases"].update(v["aliases"])
            t["sources"].add("vk")
            for f in ("vk_id", "vk_screen"):
                t[f] = t[f] or v[f]
            t["facts"] += v["facts"]
            t["bday_api"] += v["bday_api"]
            t["bday_chat"] += v["bday_chat"]
            t["bday_unparsed"] += v["bday_unparsed"]
            drop.add(id(v))
            stats["merged_cross"] += 1
    return [c for c in cands if id(c) not in drop], stats


def finalize(c: Dict) -> Dict:
    name = _primary_name(c)
    aliases = set(c["aliases"]) | set(c["names"])
    if c["tg_username"]:
        aliases.add("@" + c["tg_username"].lstrip("@"))
    if c["vk_screen"] and not _VK_NOISE.match(c["vk_screen"]):
        aliases.add(c["vk_screen"])
    seen, alias_list = {name.lower()}, []
    for a in sorted(aliases):
        if a and a.lower() not in seen:
            seen.add(a.lower())
            alias_list.append(a)
    facts, uniq = [], set()
    for t, f in c["facts"]:
        h = (t, fact_hash(f))
        if h not in uniq:
            uniq.add(h)
            facts.append((t, f, h[1]))
    bd = _birthday(c)
    return {"name": name, "aliases": alias_list, "tg_id": c["tg_id"],
            "tg_username": c["tg_username"], "vk_id": c["vk_id"], "birthday": bd[0] if bd else None,
            "bday_known": bd[1] if bd else True,
            "bday_origin": "api" if c["bday_api"] else "chat" if bd else None,
            "facts": facts, "sources": sorted(c["sources"]), "bday_unparsed": c["bday_unparsed"]}


def fact_text(name: str, fact: str) -> str:
    """Факт без имени бесполезен в контексте бота («любит кофе» — кто?).
    Если имени (по первым 4 буквам имени) в факте нет — ставим префикс."""
    stem = name_key(name.split()[0])[:4]
    return fact if stem and stem in name_key(fact) else "%s: %s" % (name.split()[0], fact)


def related_to(p: Dict) -> str:
    return " | ".join([p["name"]] + p["aliases"])


def memory_key(person_id: int, type_: str, h8: str) -> str:
    return "person_%d_%s_%s" % (person_id, type_, h8)


# ── БД ──────────────────────────────────────────────────────────────────────

def resolve_existing(user_id: str, p: Dict, idless_cache: List[Dict], stats: Counter) -> Optional[Dict]:
    pt = prepo.find_by_tg(user_id, p["tg_id"]) if p["tg_id"] else None
    pv = prepo.find_by_vk(user_id, p["vk_id"]) if p["vk_id"] else None
    if pt and pv and pt["id"] != pv["id"]:
        stats["conflict_two_existing"] += 1
        return pt
    if pt or pv:
        return pt or pv
    if not p["tg_id"] and not p["vk_id"]:
        keys = {name_key(p["name"])} | {name_key(a) for a in p["aliases"]}
        primary = name_key(p["name"])
        hits = []
        for q in idless_cache:
            alias_keys = {name_key(a) for a in (q.get("aliases") or [])}
            is_idless = not q.get("tg_id") and not q.get("vk_id")
            # (а) такой же id-less человек по имени/алиасу; (б) человек с id — ТОЛЬКО если имя
            # кандидата лежит у него в алиасах (так выглядит склеенный merge'ем источник).
            # Совпадение одного имени с id-человеком — не повод склеивать.
            if (is_idless and (name_key(q["name"]) in keys or alias_keys & keys)) or primary in alias_keys:
                hits.append(q)
        if len(hits) == 1:
            return hits[0]
    return None


def counts(user_id: str) -> Dict[str, int]:
    with get_engine().connect() as c:
        row = c.execute(sa.text("""
            SELECT
              count(*) FILTER (WHERE is_current AND NOT is_archived AND review_status='confirmed'),
              count(*) FILTER (WHERE review_status='pending' AND person_id IS NOT NULL)
            FROM memories WHERE user_id = :u"""), {"u": user_id}).one()
    return {"confirmed_visible": row[0], "pending": row[1]}


def existing_keys(user_id: str) -> Set[str]:
    with get_engine().connect() as c:
        return {r[0] for r in c.execute(sa.text(
            "SELECT key_name FROM memories WHERE user_id=:u AND key_name LIKE 'person\\_%' AND NOT is_archived"
        ), {"u": user_id})}


def embed_missing(ids: List[str], batch_size: int, pause: float) -> Tuple[int, int]:
    """Эмбеддинги пачками (1 запрос Voyage на batch_size строк; free tier 3 RPM →
    пауза между пачками). Только строки с embedding IS NULL."""
    if not ids:
        return 0, 0
    with get_engine().connect() as c:
        rows = c.execute(sa.text(
            "SELECT id, fact_text, related_to, category FROM memories "
            "WHERE id = ANY(:ids) AND embedding IS NULL"), {"ids": [int(i) for i in ids]}).all()
    items = [{"memory_id": str(r.id), "embed_text": _embed_text(r.fact_text, r.related_to, r.category)}
             for r in rows]
    done = 0
    for i in range(0, len(items), batch_size):
        if i:
            time.sleep(pause)
        n = index_memories_batch(items[i:i + batch_size])
        done += n
        print("  эмбеддинги: %d/%d" % (done, len(items)), flush=True)
    return done, len(items)


async def run(dry: bool, no_embed: bool, batch_size: int, pause: float) -> int:
    user_id = env("PEOPLE_IMPORT_USER_ID")
    rows = load_staging()
    raw, gstats = group_candidates(rows)
    people_plan = [finalize(c) for c in raw]
    skipped = [p for p in people_plan if not p["facts"] and not p["birthday"]]
    people_plan = [p for p in people_plan if p["facts"] or p["birthday"]]

    st: Counter = Counter()
    idless_cache = prepo.find_named(user_id)
    keys_have = existing_keys(user_id)
    before = counts(user_id)
    repo = PgMemoryRepo()
    scope = bot_to_scope(BOT)
    new_ids: List[str] = []
    fact_types: Counter = Counter()
    fact_new: Counter = Counter()

    for p in people_plan:
        person = resolve_existing(user_id, p, idless_cache, st)
        pid = None
        if person:
            st["people_existing"] += 1
            pid = person["id"]
            if not dry:
                if prepo.fill_missing(person, aliases=p["aliases"], birthday=p["birthday"],
                                      tg_id=p["tg_id"], tg_username=p["tg_username"], vk_id=p["vk_id"],
                                      birthday_year_known=p["bday_known"]):
                    st["people_updated"] += 1
        else:
            st["people_new"] += 1
            if not dry:
                pid = prepo.create(user_id, p["name"], p["aliases"], birthday=p["birthday"],
                                   tg_id=p["tg_id"], tg_username=p["tg_username"], vk_id=p["vk_id"],
                                   birthday_year_known=p["bday_known"])
        if p["birthday"]:
            st["bday_from_" + p["bday_origin"]] += 1
        st["bday_unparsed"] += p["bday_unparsed"]
        if len(p["sources"]) > 1:
            st["people_both_sources"] += 1

        for type_, fact, h8 in p["facts"]:
            fact_types[type_] += 1
            key = memory_key(pid, type_, h8) if pid is not None else None
            is_new = key is None or key not in keys_have
            fact_new[type_] += is_new
            if dry:
                continue
            mid, was_updated = await repo.upsert(
                fact_text(p["name"], fact), key, CATEGORY, scope, related_to(p), SOURCE, user_id,
                person_id=pid, review_status="pending", index=False,
            )
            new_ids.append(str(mid))
            st["facts_updated" if was_updated else "facts_created"] += 1

    print("— staging: строк=%d, кандидатов-людей=%d" % (len(rows), len(raw)))
    print("— люди: новых=%d, уже были=%d, дополнено=%d, пропущено (нет фактов и ДР)=%d"
          % (st["people_new"], st["people_existing"], st["people_updated"], len(skipped)))
    print("— склейка VK↔TG: склеено=%d, людей из обоих источников=%d, id-less неоднозначных=%d, конфликтов=%d"
          % (gstats["merged_cross"], st["people_both_sources"], gstats["ambiguous_idless"],
             st["conflict_two_existing"]))
    print("— ДР в people.birthday: из API=%d, из переписки=%d; нераспознанных ДР из переписки=%d"
          % (st["bday_from_api"], st["bday_from_chat"], st["bday_unparsed"]))
    print("— факты по type (всего / новых): " + ", ".join(
        "%s %d/%d" % (t, fact_types[t], fact_new[t]) for t in sorted(fact_types)))
    print("— факты всего=%d, новых=%d" % (sum(fact_types.values()), sum(fact_new.values())))
    if dry:
        print("DRY-RUN: в БД ничего не писал")
        return 0

    print("— факты записаны: создано=%d, обновлено=%d" % (st["facts_created"], st["facts_updated"]))
    if not no_embed:
        done, total = embed_missing(new_ids, batch_size, pause)
        print("— эмбеддинги: %d/%d (остаток подберёт scripts/migrate_memory_embeddings.py)" % (done, total))
    after = counts(user_id)
    ok = after["confirmed_visible"] == before["confirmed_visible"]
    print("— проверка: confirmed-видимых до=%d после=%d %s; pending до=%d после=%d (уникальных фактов=%d)"
          % (before["confirmed_visible"], after["confirmed_visible"], "OK" if ok else "ИЗМЕНИЛОСЬ!",
             before["pending"], after["pending"], sum(fact_types.values())))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-embed", action="store_true", help="не считать эмбеддинги (подберёт backfill)")
    ap.add_argument("--batch-size", type=int, default=20, help="строк на один запрос Voyage")
    ap.add_argument("--sleep", type=float, default=21.0, help="пауза между пачками (Voyage free 3 RPM)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(run(a.dry_run, a.no_embed, a.batch_size, a.sleep)))
