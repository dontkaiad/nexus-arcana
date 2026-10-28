"""Исправление «странных» карточек в Людях (пустое имя, ник владельца, удалённые аккаунты).

Без LLM. Два шага:
  plan  (локально, нужен экспорт): для pending-людей с плохим именем решает, что делать
        — rename (имя из названия/реплик собеседника или «Удалённый аккаунт · <платформа>»),
        to_self (чат с самой собой → факты в «Я»), delete_empty (пустая карточка без фактов и ДР),
        skip_protected (есть confirmed/manual факты — не трогаем), skip_no_chat.
  apply (на проде, через репозиторий): выполняет план; --dry-run только считает.
Печатает только счётчики. confirmed/manual факты и confirmed-людей не трогает никогда.

  python3 -m tools.people_import.fix_people plan  --tg … --vk … --people data/people_now.json
  python3 -m tools.people_import.fix_people apply data/fix_plan.json [--dry-run] [--threshold 0.9]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import sqlalchemy as sa

from tools.people_import.chats import classify_chat, load_chats, usable_name
from tools.people_import.common import DATA_DIR, env
from tools.people_import.selfblock import SelfBlock, load_selfblock

from core.db import get_engine
from core.repos import pg_people_repo as prepo


# ── план (локально) ─────────────────────────────────────────────────────────

def plan_fixes(chats, people_rows: List[Dict], sb: SelfBlock) -> Dict:
    """people_rows: [{id, name, tg_id, vk_id, facts, nonpending_facts, has_birthday}] — pending, не «Я»."""
    actions: List[Dict] = []
    stat: Counter = Counter()
    for p in people_rows:
        if usable_name(p["name"], sb.is_self_name):
            continue                                              # имя нормальное — не трогаем
        src, cid = ("tg", p["tg_id"]) if p.get("tg_id") else ("vk", p.get("vk_id"))
        chat = (chats.get(src) or {}).get(cid)
        if p.get("nonpending_facts"):
            stat["skip_protected"] += 1
            actions.append({"id": p["id"], "action": "skip_protected"})
            continue
        if chat is None:
            stat["skip_no_chat"] += 1
            actions.append({"id": p["id"], "action": "skip_no_chat"})
            continue
        kind, name = classify_chat(chat, sb)
        if kind == "self":
            act = {"id": p["id"], "action": "to_self" if p["facts"] else "delete_empty"}
        elif p["facts"] == 0 and not p.get("has_birthday"):
            act = {"id": p["id"], "action": "delete_empty"}
        else:
            act = {"id": p["id"], "action": "rename", "name": name,
                   "deleted_account": name.startswith("Удалённый аккаунт")}
        stat[act["action"] + ("_deleted" if act.get("deleted_account") else "")] += 1
        stat["facts_" + act["action"]] += p["facts"]
        actions.append(act)
    return {"actions": actions, "stat": dict(stat)}


# ── применение (прод) ───────────────────────────────────────────────────────

def dedupe_self_in_db(user_id: str, threshold: float, dry: bool, embed=None) -> Dict:
    """Схлопывает pending-факты «Я» после переноса в неё фактов из чатов с другим аккаунтом.
    Эмбеддинги берём от ЧИСТОГО текста факта (как при импорте), а НЕ хранимые в БД: те посчитаны
    по «факт + алиасы + категория», общий хвост делает все факты «Я» похожими, и порог 0,9 склеил бы
    разные факты (dry-run на проде показал бы 782 ложных удаления). Нужен вызов Voyage (не Anthropic).
    Подтверждённые не трогает. Без embed используется Voyage."""
    from tools.people_import.import_memory import cluster_facts, embed_texts
    embed = embed or embed_texts
    me = prepo.find_self(user_id)
    if not me:
        return {"merged_groups": 0, "removed": 0}
    with get_engine().connect() as c:
        rows = c.execute(sa.text(
            "SELECT id, fact_text, key_name, mention_count, first_mention_date, last_mention_date, "
            "source_message_date FROM memories "
            "WHERE person_id = :p AND review_status = 'pending' AND source = 'auto' AND NOT is_archived"),
            {"p": me["id"]}).all()
    facts = [{"type": prepo.fact_type(r.key_name), "fact": r.fact_text,
              "date": r.last_mention_date or r.source_message_date, "row": r} for r in rows]
    if len(facts) < 2:
        return {"merged_groups": 0, "removed": 0}
    vecs = embed([f["fact"] for f in facts])
    if not vecs or len(vecs) != len(facts):
        return {"merged_groups": 0, "removed": 0, "skipped_no_embeddings": 1}
    clusters = [cl for cl in cluster_facts(facts, vecs, threshold) if len(cl) > 1]
    removed = sum(len(cl) - 1 for cl in clusters)
    if dry or not clusters:
        return {"merged_groups": len(clusters), "removed": removed}
    with get_engine().begin() as c:
        for cl in clusters:
            rep, others = facts[cl[0]]["row"], [facts[i]["row"] for i in cl[1:]]
            members = [rep] + others
            firsts = [m.first_mention_date or m.source_message_date for m in members if m.first_mention_date or m.source_message_date]
            lasts = [m.last_mention_date or m.source_message_date for m in members if m.last_mention_date or m.source_message_date]
            c.execute(sa.text(
                "UPDATE memories SET mention_count = :n, first_mention_date = :f, last_mention_date = :l, "
                "updated_at = now() WHERE id = :i"),
                {"n": sum(m.mention_count or 1 for m in members), "f": min(firsts) if firsts else None,
                 "l": max(lasts) if lasts else None, "i": rep.id})
            c.execute(sa.text("DELETE FROM memories WHERE id = ANY(:ids)"), {"ids": [o.id for o in others]})
    return {"merged_groups": len(clusters), "removed": removed}


def apply_plan(user_id: str, plan: Dict, dry: bool, threshold: float = 0.9, embed=None) -> Dict:
    st: Counter = Counter()
    for a in plan["actions"]:
        pid, act = a["id"], a["action"]
        if act.startswith("skip"):
            st[act] += 1
            continue
        person = prepo.get_person(user_id, pid)
        if not person or person["status"] != "pending" or person.get("is_self"):
            st["skip_changed"] += 1                              # человек уже изменён/подтверждён с момента плана
            continue
        if act == "rename":
            if not dry:
                prepo.update_person(user_id, pid, {"name": a["name"]})
            st["rename"] += 1
            st["rename_deleted_account"] += bool(a.get("deleted_account"))
        elif act == "to_self":
            if not dry:
                try:
                    stats = prepo.absorb_into_self(user_id, pid)
                except ValueError:
                    st["skip_protected"] += 1
                    continue
                if stats is None:
                    st["skip_no_self"] += 1
                    continue
                st["facts_moved_to_self"] += stats["moved"]
                st["facts_dropped_dup"] += stats["dropped_duplicates"]
            else:
                st["facts_moved_to_self"] += person["counts"].get("pending", 0)
            st["to_self"] += 1
        elif act == "delete_empty":
            if person["counts"].get("pending", 0) + person["counts"].get("confirmed", 0) + person["counts"].get("rejected", 0):
                st["skip_changed"] += 1
                continue
            if not dry:
                with get_engine().begin() as c:
                    c.execute(sa.text("DELETE FROM people WHERE id = :i AND status = 'pending' AND NOT is_self"), {"i": pid})
            st["delete_empty"] += 1
    if st["to_self"]:                      # дедуп нужен ТОЛЬКО если в «Я» что-то перенесли; иначе «Я» не трогаем
        st.update({"dedupe_" + k: v for k, v in dedupe_self_in_db(user_id, threshold, dry, embed).items()})
    return dict(st)


def counts(user_id: str) -> Dict:
    with get_engine().connect() as c:
        r = c.execute(sa.text("""
            SELECT (SELECT count(*) FROM people WHERE user_id=:u),
                   (SELECT count(*) FROM people WHERE user_id=:u AND status='pending' AND NOT is_self),
                   (SELECT count(*) FROM memories WHERE user_id=:u AND review_status='pending' AND person_id IS NOT NULL),
                   (SELECT count(*) FROM memories WHERE user_id=:u AND is_current AND NOT is_archived AND review_status='confirmed')"""),
            {"u": user_id}).one()
    return {"людей": r[0], "pending_людей": r[1], "pending_фактов": r[2], "confirmed_видимых": r[3]}


def fetch_people(user_id: str) -> List[Dict]:
    """pending-люди с id чата + счётчики фактов (имя нужно для проверки «плохое ли»; наружу не печатаем)."""
    with get_engine().connect() as c:
        rows = c.execute(sa.text("""
            SELECT p.id, p.name, p.tg_id, p.vk_id, (p.birthday IS NOT NULL) AS has_birthday,
                   (SELECT count(*) FROM memories m WHERE m.person_id = p.id) AS facts,
                   (SELECT count(*) FROM memories m WHERE m.person_id = p.id
                      AND (m.review_status <> 'pending' OR m.source <> 'auto')) AS nonpending_facts
            FROM people p WHERE p.user_id = :u AND p.status = 'pending' AND NOT p.is_self
              AND (p.tg_id IS NOT NULL OR p.vk_id IS NOT NULL) ORDER BY p.id"""), {"u": user_id}).all()
    return [dict(r._mapping) for r in rows]


def _print_stat(title: str, st: Dict) -> None:
    print(title)
    for k in sorted(st):
        print("  %-28s %s" % (k, st[k]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--tg", default="")
    pl.add_argument("--vk", default="")
    pl.add_argument("--people", required=True, help="JSON со списком pending-людей (fetch_people)")
    pl.add_argument("--out", default=str(DATA_DIR / "fix_plan.json"))
    pa = sub.add_parser("apply")
    pa.add_argument("plan")
    pa.add_argument("--dry-run", action="store_true")
    pa.add_argument("--threshold", type=float, default=0.9)
    pf = sub.add_parser("fetch")
    pf.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "fetch":
        Path(a.out).write_text(json.dumps(fetch_people(env("PEOPLE_IMPORT_USER_ID")), ensure_ascii=False), encoding="utf-8")
        print("людей выгружено:", len(json.loads(Path(a.out).read_text(encoding="utf-8"))))
    elif a.cmd == "plan":
        chats = load_chats(a.tg, a.vk)
        sb = load_selfblock(getattr(chats.get("tg"), "owner", None))
        pl_ = plan_fixes(chats, json.loads(Path(a.people).read_text(encoding="utf-8")), sb)
        Path(a.out).write_text(json.dumps(pl_, ensure_ascii=False), encoding="utf-8")
        _print_stat("ПЛАН (по случаям):", pl_["stat"])
    else:
        uid = env("PEOPLE_IMPORT_USER_ID")
        before = counts(uid)
        res = apply_plan(uid, json.loads(Path(a.plan).read_text(encoding="utf-8")), a.dry_run, a.threshold)
        _print_stat("ПРИМЕНЕНО%s:" % (" (dry-run, в БД ничего не писал)" if a.dry_run else ""), res)
        print("до:   ", before)
        print("после:", counts(uid))
