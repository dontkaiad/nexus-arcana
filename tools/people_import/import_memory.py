"""staging → People domain (ADR-0027): `people` + pending-факты в 🧠 Памяти.

Правила:
  • Человек = конкретный чат: tg_id или vk_id обязателен. Id-less людей не создаём.
  • Факты о владельце (about=kai, либо чат с её же аккаунтом) идут в единственную карточку «Я»
    (people.is_self, status=confirmed); отдельный человек с её именем/id не создаётся.
  • Факты «Я» дедуплицируются по эмбеддингам (cosine ≥ порога, один type, одинаковая полярность):
    один факт + mention_count + даты первого/последнего упоминания.
  • Факты других людей: person_id, review_status=pending, source=auto, ключ
    person_{person_id}_{type}_{hash8}; происхождение (чат, дата, автор цитаты, платформа) сохраняется.
  • ДР → только people.birthday. Повторный запуск идемпотентен.

.env: PEOPLE_IMPORT_USER_ID (+ PEOPLE_IMPORT_SELF_* для карточки «Я», см. selfblock).
  python3 -m tools.people_import.import_memory --dry-run
  python3 -m tools.people_import.import_memory [--no-embed] [--dedupe-threshold 0.9]
  python3 -m tools.people_import.import_memory --dedupe-report      # подбор порога
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
from collections import Counter, defaultdict
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import sqlalchemy as sa

from tools.people_import.common import env, fact_hash, load_staging, name_key, parse_birthday
from tools.people_import.selfblock import SelfBlock, load_selfblock

from core.db import get_engine
from core.memory_rag import _embed_text, index_memories_batch
from core.people import fact_text as _fact_text, related_to_str
from core.repos import pg_client_links as client_links
from core.repos import pg_people_repo as prepo
from core.repos.pg_memory_repo import PgMemoryRepo, bot_to_scope

CATEGORY = "👥 Люди"
BOT = "☀️ Nexus"
SOURCE = "auto"
SELF_NAME = "Кай"
_VK_NOISE = re.compile(r"^id\d+$")
_NEG = re.compile(r"(?<!\w)(не|нет|без|никогда|ни)(?!\w)")


def _d(s) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


# ── планирование (чистые функции, без БД) ───────────────────────────────────

def _new_cand() -> Dict:
    return {"names": Counter(), "aliases": set(), "tg_id": None, "tg_username": None,
            "vk_id": None, "vk_screen": None, "sources": set(), "facts": [],
            "bday_api": [], "bday_chat": [], "bday_unparsed": 0, "is_self": False}


def _fact_of(r: Dict) -> Dict:
    return {"type": r["type"], "fact": r["fact"].strip(), "quote": (r.get("quote") or "").strip(),
            "date": _d(r.get("source_message_date")), "chat_id": r.get("source_chat_id"),
            "author": r.get("quote_author"), "platform": r.get("source")}


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
        c["facts"].append(_fact_of(r))


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


def group_candidates(rows: List[Dict], sb: Optional[SelfBlock] = None) -> Tuple[List[Dict], Dict[str, int]]:
    """Строки staging → кандидаты-люди. Возвращает (кандидаты, статистика)."""
    sb = sb or load_selfblock()
    stats = {"merged_cross": 0, "skipped_no_id": 0}
    by_ident: Dict[Tuple, Dict] = {}
    cands: List[Dict] = []
    for r in rows:
        is_self = (r.get("about") == "kai"
                   or sb.is_self(name=r["name"], tg_id=r.get("tg_id"), vk_id=r.get("vk_id")))
        if is_self:
            ident: Optional[Tuple] = ("self",)
        elif r.get("tg_id"):
            ident = ("tg", r["tg_id"])
        elif r.get("vk_id"):
            ident = ("vk", r["vk_id"])
        else:
            ident = None
        if ident is None:                          # человек = конкретный чат; без id не создаём
            stats["skipped_no_id"] += 1
            continue
        c = by_ident.get(ident)
        if c is None:
            c = by_ident[ident] = _new_cand()
            c["is_self"] = is_self
            cands.append(c)
        if is_self:                                # у «Я» имя/id берём из блок-листа, не из чатов
            c["names"][SELF_NAME] += 1
            c["sources"].add(r.get("source") or "")
            if r["type"] != "birthday":
                c["facts"].append(_fact_of(r))
        else:
            _absorb(c, r)

    # автосклейка VK↔TG: имя+фамилия И (ДР ИЛИ tg_username == vk screen_name), взаимно-однозначно
    tgs = [c for c in cands if not c["is_self"] and c["sources"] == {"tg"}]
    vks = [c for c in cands if not c["is_self"] and c["sources"] == {"vk"}]

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


# ── дедуп фактов «Я» ────────────────────────────────────────────────────────

def _tokens(s: str) -> Set[str]:
    return {w[:5] for w in re.findall(r"\w+", s.lower().replace("ё", "е")) if len(w) > 2}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def cluster_facts(facts: List[Dict], vecs: Sequence[Sequence[float]], threshold: float) -> List[List[int]]:
    """Жадная кластеризация: факт вступает в первый кластер, чей представитель (самое свежее
    упоминание) похож по cosine ≥ порога. Только внутри одного type и с одинаковой полярностью
    («любит чай» / «не любит чай» — разные факты, хотя эмбеддинги почти совпадают)."""
    import numpy as np
    n = len(facts)
    order = sorted(range(n), key=lambda i: (facts[i]["date"] or date.min), reverse=True)
    m = np.array([vecs[i] for i in range(n)], dtype="float32")
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    clusters: List[List[int]] = []
    reps: List[int] = []
    for i in order:
        placed = False
        for ci, r in enumerate(reps):
            if (facts[i]["type"] == facts[r]["type"]
                    and bool(_NEG.search(facts[i]["fact"].lower())) == bool(_NEG.search(facts[r]["fact"].lower()))
                    and float(m[i] @ m[r]) >= threshold):
                clusters[ci].append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
            reps.append(i)
    return clusters


def exact_clusters(facts: List[Dict]) -> List[List[int]]:
    """Fallback без эмбеддингов: совпадение нормализованного текста и type."""
    by: Dict[Tuple, List[int]] = defaultdict(list)
    for i, f in enumerate(facts):
        by[(f["type"], fact_hash(f["fact"]))].append(i)
    return list(by.values())


def merge_clusters(facts: List[Dict], clusters: List[List[int]]) -> List[Dict]:
    out = []
    for cl in clusters:
        members = sorted((facts[i] for i in cl), key=lambda f: (f["date"] or date.min), reverse=True)
        rep = dict(members[0])                      # самое свежее упоминание — представитель
        dates = [f["date"] for f in members if f["date"]]
        rep["mentions"] = len(members)
        rep["first"], rep["last"] = (min(dates), max(dates)) if dates else (None, None)
        out.append(rep)
    return out


def embed_texts(texts: List[str], batch: int = 128, pause: float = 21.0) -> List[List[float]]:
    """Voyage-эмбеддинги пачками (1 запрос на batch; при низком лимите RPM — пауза). [] если недоступно."""
    from core.rag import _embed
    out: List[List[float]] = []
    for i in range(0, len(texts), batch):
        if i:
            time.sleep(pause)
        v = _embed(texts[i:i + batch], input_type="document")
        if len(v) != len(texts[i:i + batch]):
            return []
        out += v
    return out


def dedupe_self(facts: List[Dict], threshold: float,
                embed: Optional[Callable[[List[str]], List[List[float]]]] = None) -> Tuple[List[Dict], str]:
    """Факты «Я» → (дедуплицированные, способ). Без эмбеддингов — точный дедуп по тексту."""
    if not facts:
        return [], "none"
    vecs = (embed or embed_texts)([f["fact"] for f in facts])
    if vecs and len(vecs) == len(facts):
        return merge_clusters(facts, cluster_facts(facts, vecs, threshold)), "embeddings"
    return merge_clusters(facts, exact_clusters(facts)), "exact"


def threshold_report(facts: List[Dict], vecs: Sequence[Sequence[float]],
                     thresholds: Sequence[float] = (0.80, 0.85, 0.88, 0.90, 0.92, 0.95)) -> List[Dict]:
    """Для подбора порога: сколько кластеров и насколько «похожи по словам» объединённые факты
    (низкий средний Jaccard при сильном сжатии = подозрительные склейки). Тексты не печатаем."""
    rows = []
    for t in thresholds:
        cl = cluster_facts(facts, vecs, t)
        merged = [c for c in cl if len(c) > 1]
        jac = [_jaccard(facts[c[0]]["fact"], facts[i]["fact"]) for c in merged for i in c[1:]]
        rows.append({"порог": t, "кластеров": len(cl), "сжатие": "%.0f%%" % (100 * (1 - len(cl) / max(len(facts), 1))),
                     "объединённых пар": len(jac), "средний Jaccard слов": round(sum(jac) / len(jac), 2) if jac else None,
                     "пар со словесным перекрытием <0.2": sum(1 for j in jac if j < 0.2)})
    return rows


# ── финализация ─────────────────────────────────────────────────────────────

def finalize(c: Dict, sb: Optional[SelfBlock] = None, threshold: float = 0.9,
             embed: Optional[Callable] = None) -> Dict:
    sb = sb or load_selfblock()
    if c["is_self"]:
        facts, dedupe_how = dedupe_self(c["facts"], threshold, embed)
        name, aliases = SELF_NAME, sb.display_names()
        ids = {"tg_id": sb.primary_tg_id(), "tg_username": sb.primary_username(), "vk_id": sb.primary_vk_id(),
               "accounts": sb.account_list()}                # все аккаунты владельца → person_accounts «Я»
        bd, bd_origin, bday_known = None, None, True
    else:
        name = _primary_name(c)
        al = set(c["aliases"]) | set(c["names"])
        if c["tg_username"]:
            al.add("@" + c["tg_username"].lstrip("@"))
        if c["vk_screen"] and not _VK_NOISE.match(c["vk_screen"]):
            al.add(c["vk_screen"])
        seen, aliases = {name.lower()}, []
        for a in sorted(al):
            if a and a.lower() not in seen:
                seen.add(a.lower())
                aliases.append(a)
        facts, uniq = [], set()
        for f in c["facts"]:
            h = (f["type"], fact_hash(f["fact"]))
            if h not in uniq:
                uniq.add(h)
                facts.append({**f, "mentions": 1, "first": f["date"], "last": f["date"]})
        ids = {"tg_id": c["tg_id"], "tg_username": c["tg_username"], "vk_id": c["vk_id"]}
        b = _birthday(c)
        bd, bday_known = (b[0], b[1]) if b else (None, True)
        bd_origin = "api" if c["bday_api"] else "chat" if b else None
        dedupe_how = None
    for f in facts:
        f["h8"] = fact_hash(f["fact"])
    return {"name": name, "aliases": aliases, **ids, "is_self": c["is_self"], "birthday": bd,
            "bday_known": bday_known, "bday_origin": bd_origin, "facts": facts,
            "sources": sorted(c["sources"]), "bday_unparsed": c["bday_unparsed"],
            "raw_facts": len(c["facts"]), "dedupe": dedupe_how}


fact_text = _fact_text  # общая реализация: core.people.fact_text


def related_to(p: Dict) -> str:
    return related_to_str(p["name"], p["aliases"])


def memory_key(person_id: int, type_: str, h8: str) -> str:
    return "person_%d_%s_%s" % (person_id, type_, h8)


def provenance(f: Dict) -> Dict:
    return {"source_chat_id": f.get("chat_id"), "source_message_date": f.get("date"),
            "quote_author": f.get("author"), "source_platform": f.get("platform"),
            "mention_count": f.get("mentions", 1), "first_mention_date": f.get("first"),
            "last_mention_date": f.get("last")}


# ── БД ──────────────────────────────────────────────────────────────────────

def resolve_existing(user_id: str, p: Dict) -> Optional[Dict]:
    if p["is_self"]:
        return prepo.find_self(user_id)
    pt = prepo.find_by_tg(user_id, p["tg_id"]) if p["tg_id"] else None
    pv = prepo.find_by_vk(user_id, p["vk_id"]) if p["vk_id"] else None
    return pt or pv


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
    """Эмбеддинги записанных фактов пачками (только embedding IS NULL)."""
    if not ids:
        return 0, 0
    with get_engine().connect() as c:
        rows = c.execute(sa.text(
            "SELECT id, fact_text, related_to, category, person_id FROM memories "
            "WHERE id = ANY(:ids) AND embedding IS NULL"), {"ids": [int(i) for i in ids]}).all()
    items = [{"memory_id": str(r.id), "embed_text": _embed_text(r.fact_text, r.related_to, r.category, r.person_id)} for r in rows]
    done = 0
    for i in range(0, len(items), batch_size):
        if i:
            time.sleep(pause)
        done += index_memories_batch(items[i:i + batch_size])
        print("  эмбеддинги: %d/%d" % (done, len(items)), flush=True)
    return done, len(items)


async def run(dry: bool, no_embed: bool, batch_size: int, pause: float, threshold: float = 0.9,
              embed: Optional[Callable] = None, sb: Optional[SelfBlock] = None) -> int:
    user_id = env("PEOPLE_IMPORT_USER_ID")
    sb = sb or load_selfblock()
    rows = load_staging()
    raw, gstats = group_candidates(rows, sb)
    plan = [finalize(c, sb, threshold, embed) for c in raw]
    skipped = [p for p in plan if not p["facts"] and not p["birthday"] and not p["is_self"]]
    plan = [p for p in plan if p["facts"] or p["birthday"] or p["is_self"]]

    st: Counter = Counter()
    keys_have = existing_keys(user_id)
    before = counts(user_id)
    repo = PgMemoryRepo()
    scope = bot_to_scope(BOT)
    new_ids: List[str] = []
    fact_types: Counter = Counter()
    fact_new: Counter = Counter()

    for p in plan:
        person = resolve_existing(user_id, p)
        pid = None
        if p["is_self"]:
            st["self_facts_raw"] += p["raw_facts"]
            st["self_facts_deduped"] += len(p["facts"])
            if p["dedupe"]:
                st["dedupe_" + p["dedupe"]] += 1
            if not dry:
                pid = prepo.get_or_create_self(user_id, p["name"], p["aliases"], tg_id=p["tg_id"],
                                               tg_username=p["tg_username"], vk_id=p["vk_id"],
                                               accounts=p.get("accounts"))
                client_links.ensure_self_link(user_id)      # клиент `self` ↔ «Я» без вопросов
            else:
                pid = person["id"] if person else None
        elif person:
            st["people_existing"] += 1
            pid = person["id"]
            if not dry and prepo.fill_missing(person, aliases=p["aliases"], birthday=p["birthday"],
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

        # related_to — от УЖЕ существующей карточки (её имя и алиасы), а не от кандидата: у VK-диалога без
        # названия имя кандидата пустое, и факты получали пустой related_to, хотя у человека имя есть.
        rel = related_to(p)
        if person and not p["is_self"]:
            seen_al = list(dict.fromkeys(list(person.get("aliases") or []) + p["aliases"]))
            rel = related_to_str(person.get("name") or p["name"], seen_al)
        for f in p["facts"]:
            key = memory_key(pid, f["type"], f["h8"]) if pid is not None else None
            is_new = key is None or key not in keys_have
            tag = ("self:" if p["is_self"] else "") + f["type"]
            fact_types[tag] += 1
            fact_new[tag] += is_new
            if dry or not is_new:
                continue
            text_ = f["fact"]    # без префикса «Имя: » — имя рисуется из person
            mid, was_updated = await repo.upsert(
                text_, key, CATEGORY, scope, rel, SOURCE, user_id, person_id=pid,
                review_status="pending", index=False,
                source_quote=f["quote"] or None, provenance=provenance(f),
            )
            new_ids.append(str(mid))
            st["facts_updated" if was_updated else "facts_created"] += 1

    print("— staging: строк=%d, кандидатов=%d" % (len(rows), len(raw)))
    print("— люди: новых=%d, уже были=%d, дополнено=%d, пропущено (нет фактов и ДР)=%d, строк без id пропущено=%d"
          % (st["people_new"], st["people_existing"], st["people_updated"], len(skipped), gstats["skipped_no_id"]))
    print("— склейка VK↔TG: склеено=%d, людей из обоих источников=%d" % (gstats["merged_cross"], st["people_both_sources"]))
    print("— «Я»: фактов до дедупа=%d → после=%d (%s)" % (
        st["self_facts_raw"], st["self_facts_deduped"],
        ", ".join(k.replace("dedupe_", "") for k in st if k.startswith("dedupe_")) or "—"))
    print("— ДР в people.birthday: из API=%d, из переписки=%d; нераспознанных=%d"
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


def dedupe_report(sb: Optional[SelfBlock] = None) -> None:
    sb = sb or load_selfblock()
    raw, _ = group_candidates(load_staging(), sb)
    me = next((c for c in raw if c["is_self"]), None)
    if not me:
        print("в staging нет фактов о владельце")
        return
    facts = me["facts"]
    vecs = embed_texts([f["fact"] for f in facts])
    if not vecs:
        print("эмбеддинги недоступны (нет VOYAGE_API_KEY?)")
        return
    print("фактов «Я» до дедупа: %d" % len(facts))
    for r in threshold_report(facts, vecs):
        print(r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-embed", action="store_true", help="не считать эмбеддинги записанных фактов")
    ap.add_argument("--batch-size", type=int, default=20, help="строк на один запрос Voyage")
    ap.add_argument("--sleep", type=float, default=21.0, help="пауза между пачками (лимит зависит от тарифа ключа)")
    ap.add_argument("--dedupe-threshold", type=float, default=0.9)
    ap.add_argument("--dedupe-report", action="store_true", help="таблица порогов для подбора, без записи")
    a = ap.parse_args()
    if a.dedupe_report:
        dedupe_report()
    else:
        raise SystemExit(asyncio.run(run(a.dry_run, a.no_embed, a.batch_size, a.sleep, a.dedupe_threshold)))
