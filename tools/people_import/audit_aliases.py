"""Аудит «Других имён» (people.aliases): алиасы, совпадающие с именем/алиасом ДРУГОГО человека или «Я».

  python -m tools.people_import.audit_aliases            # только отчёт: числа и id, имён не печатает
  python -m tools.people_import.audit_aliases --apply    # + удалить алиасы, совпавшие с именем/алиасом «Я» (self)

Остальные совпадения НЕ трогаются — печатаются списком id для ручной правки.
Виды совпадения (alias человека X ↔ Y ≠ X):
  self_exact   — целиком равен имени/алиасу «Я»                 → удаляется с --apply
  self_token   — равен слову многословного имени «Я»             → удаляется с --apply
  other_exact  — целиком равен имени/алиасу другого человека     → только отчёт
  other_token  — равен слову многословного имени другого         → только отчёт (шумно: общие отчества/фамилии)
Происхождение в БД не хранится; печатаются признаки: позиция алиаса в списке (импорт кладёт найденные имена
в порядке частоты, склейка `absorb` дописывает имя и алиасы поглощённого В КОНЕЦ), число слов, статус Y."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from typing import Dict, List, Set

import sqlalchemy as sa

from core.db import get_engine
from core.people import _tokens, aliases_norm_for
from core.ru_morph import strip_case_ending


def _key(word: str) -> str:
    return strip_case_ending(word) if len(word) >= 4 else word


def _whole(s: str) -> str:
    return " ".join(_key(w) for w in _tokens(s))


def audit(people: List[Dict]) -> List[Dict]:
    """people: [{id, name, aliases, is_self, status}] одного владельца → список находок."""
    ident: Dict[int, Set[str]] = {}      # целиком: имя и алиасы
    words: Dict[int, Set[str]] = {}      # слова многословного ИМЕНИ
    for p in people:
        ident[p["id"]] = {k for k in [_whole(p["name"])] + [_whole(a) for a in p["aliases"]] if k}
        t = _tokens(p["name"])
        words[p["id"]] = {_key(w) for w in t} if len(t) > 1 else set()
    out = []
    for x in people:
        if x["is_self"]:              # алиасы самого «Я» — его собственные, не аудируем
            continue
        al = x["aliases"]
        for idx, a in enumerate(al):
            wa = _whole(a)
            if not wa:
                continue
            for y in people:
                if y["id"] == x["id"]:
                    continue
                kind = None
                if wa in ident[y["id"]]:
                    kind = "exact"
                elif " " not in wa and wa in words[y["id"]]:
                    kind = "token"
                if kind:
                    out.append({"person": x["id"], "alias_idx": idx, "alias_n": len(al), "alias_words": len(_tokens(a)),
                                "other": y["id"], "other_self": y["is_self"], "other_status": y["status"],
                                "kind": ("self_" if y["is_self"] else "other_") + kind, "alias": a})
    return out


def group_entries(found: List[Dict]) -> List[Dict]:
    """Находки по парам → записи (person, alias_idx): с кем совпало, совпало ли с «Я», целиком или по слову."""
    g: Dict[tuple, Dict] = {}
    for f in found:
        e = g.setdefault((f["person"], f["alias_idx"]), {
            "person": f["person"], "alias_idx": f["alias_idx"], "alias_n": f["alias_n"], "others": set(),
            "self": False, "exact": False, "rejected": False, "norm": _whole(f["alias"])})
        e["others"].add(f["other"])
        e["self"] = e["self"] or f["kind"].startswith("self_")
        e["exact"] = e["exact"] or f["kind"].endswith("exact")
        e["rejected"] = e["rejected"] or f["other_status"] == "rejected"
    cnt = Counter(e["norm"] for e in g.values())
    for e in g.values():
        e["shared"] = cnt[e["norm"]]
    return sorted(g.values(), key=lambda e: (e["person"], e["alias_idx"]))


def mutual_pairs(entries: List[Dict]) -> set:
    """X→Y и Y→X одновременно: вероятные дубли людей или остаток склейки."""
    edges = {(e["person"], o) for e in entries for o in e["others"]}
    return {tuple(sorted(p)) for p in edges if (p[1], p[0]) in edges}


def _load(c) -> Dict[str, List[Dict]]:
    by: Dict[str, List[Dict]] = defaultdict(list)
    for r in c.execute(sa.text("SELECT id, user_id, name, aliases, is_self, status FROM people ORDER BY id")):
        by[r.user_id].append({"id": r.id, "name": r.name, "aliases": list(r.aliases or []),
                              "is_self": bool(r.is_self), "status": r.status})
    return by


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", help="только этот user_id (по умолчанию все)")
    ap.add_argument("--apply", action="store_true", help="удалить self-алиасы (остальное — только отчёт)")
    a = ap.parse_args(argv)
    eng = get_engine()
    with eng.connect() as c:
        by = _load(c)
    removed = 0
    for uid, people in by.items():
        if a.user and uid != a.user:
            continue
        found = audit(people)
        entries = group_entries(found)
        total_al = sum(len(p["aliases"]) for p in people)
        selfs = [e for e in entries if e["self"]]
        oth = [e for e in entries if not e["self"] and e["exact"]]
        tok = [e for e in entries if not e["self"] and not e["exact"]]
        print("владелец: людей %d (self %d), алиасов %d" % (len(people), sum(p["is_self"] for p in people), total_al))
        print("  алиасов с совпадением: %d (%.0f%%), затронуто людей: %d" % (
            len(entries), 100.0 * len(entries) / max(1, total_al), len({e["person"] for e in entries})))
        print("  • совпали с «Я» (удаляются с --apply): %d" % len(selfs))
        print("  • целиком = имя/алиас другого человека: %d  (взаимных пар: %d; общий алиас у ≥3 человек: %d)" % (
            len(oth), len(mutual_pairs(oth)), len({e["norm"] for e in oth if e["shared"] >= 3})))
        print("  • равен слову многословного имени другого (шумно, отчества/фамилии): %d" % len(tok))
        print("  по положению в списке: первые %d, в хвосте (дописаны склейкой/повторным импортом) %d" % (
            sum(1 for e in entries if e["alias_idx"] < max(1, e["alias_n"] // 2)),
            sum(1 for e in entries if e["alias_idx"] >= max(1, e["alias_n"] // 2))))
        print("  с rejected-человеком: %d" % sum(1 for e in entries if e["rejected"]))
        print("  self-алиасы (person#позиция): " + ", ".join("%d#%d" % (e["person"], e["alias_idx"]) for e in selfs))
        print("  ручной разбор — целиком совпало (person#позиция→другие id):")
        for e in oth:
            print("    %d#%d→%s%s" % (e["person"], e["alias_idx"], ",".join(map(str, sorted(e["others"]))), " (общий)" if e["shared"] >= 3 else ""))
        print("  ручной разбор — по слову имени (person#позиция→id): " + "; ".join(
            "%d#%d→%s" % (e["person"], e["alias_idx"], ",".join(map(str, sorted(e["others"])))) for e in tok))
        if a.apply:
            drop: Dict[int, Set[int]] = defaultdict(set)
            for f in found:
                if f["kind"].startswith("self_"):
                    drop[f["person"]].add(f["alias_idx"])
            by_id = {p["id"]: p for p in people}
            with eng.begin() as c:
                for pid, idxs in drop.items():
                    p = by_id[pid]
                    new = [al for i, al in enumerate(p["aliases"]) if i not in idxs]
                    c.execute(sa.text("UPDATE people SET aliases = :a, aliases_norm = :n, updated_at = now() WHERE id = :i"),
                              {"a": new, "n": aliases_norm_for(p["name"], new), "i": pid})
                    c.execute(sa.text("UPDATE memories SET related_to = :r WHERE person_id = :i AND fact_text <> ''"),
                              {"r": " | ".join([p["name"]] + new), "i": pid})
                    removed += len(idxs)
    if a.apply:
        print("удалено self-алиасов: %d" % removed)
    else:
        print("dry-run: ничего не изменено. --apply удалит только self_*.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
