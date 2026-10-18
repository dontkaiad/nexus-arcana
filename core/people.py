"""core/people.py — pure helpers of the People domain (ADR-0027)."""
from __future__ import annotations

from datetime import date
from typing import Optional

UNKNOWN_YEAR = 1904  # заглушка года в people.birthday, когда год неизвестен


def age(birthday: Optional[date], year_known: bool = True, today: Optional[date] = None) -> Optional[int]:
    """Полных лет на `today`. None, если ДР нет или год неизвестен
    (заглушка 1904 — не настоящий год рождения; считаем возраст — врём)."""
    if birthday is None or not year_known or birthday.year == UNKNOWN_YEAR:
        return None
    today = today or date.today()
    years = today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    return years if years >= 0 else None


def birthday_iso(birthday: Optional[date], year_known: bool = True) -> Optional[str]:
    """'YYYY-MM-DD'; при неизвестном годе — 'MM-DD' c префиксом '--' (ISO 8601 partial),
    чтобы заглушка 1904 не утекала в API."""
    if birthday is None:
        return None
    if not year_known or birthday.year == UNKNOWN_YEAR:
        return "--%02d-%02d" % (birthday.month, birthday.day)
    return birthday.isoformat()


# ── кандидаты в дубли (PR-5) ─────────────────────────────────────────────────

import re as _re
from typing import Any, Dict, Iterable, List, Tuple

# Вес каждого независимого сигнала; score = 1 − Π(1 − w), max 1.0.
DUP_WEIGHTS = {"full_name": 0.7, "name_birthday": 0.6, "alias_is_name": 0.5}
DUP_LIMIT = 200


def _norm(s: str) -> str:
    return _re.sub(r"[^\w\s@]", " ", (s or "").lower().replace("ё", "е"), flags=_re.UNICODE).strip()


def _tokens(s: str) -> List[str]:
    return [t for t in _norm(s).split() if t]


def full_name_key(name: str) -> Optional[str]:
    """Имя+фамилия без учёта порядка («Мария Иванова» == «Иванова Мария»); <2 слов → None."""
    t = _tokens(name)
    return " ".join(sorted(t)) if len(t) >= 2 else None


def _same_bday(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    da, db = a.get("birthday"), b.get("birthday")
    if not da or not db or (da.month, da.day) != (db.month, db.day):
        return False
    if a.get("birthday_year_known", True) and b.get("birthday_year_known", True):
        return da.year == db.year
    return True


def _distinct_accounts(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """У обоих есть tg_id (или vk_id), и они разные → разные аккаунты, не дубль."""
    for f in ("tg_id", "vk_id"):
        if a.get(f) and b.get(f) and a[f] != b[f]:
            return True
    return False


def find_duplicates(rows: Iterable[Dict[str, Any]], limit: int = DUP_LIMIT) -> List[Dict[str, Any]]:
    """Пары-кандидаты в один человек. НИЧЕГО не склеивает — только подсказка для merge.

    Сигналы: совпала нормализованная пара имя+фамилия; общий токен имени и одинаковый ДР;
    алиас одного равен имени другого. Отклонённых (rejected) пропускаем."""
    people_ = [r for r in rows if r.get("status") != "rejected"]
    pairs: Dict[Tuple[int, int], List[str]] = {}

    def add(a, b, reason):
        if a["id"] == b["id"] or _distinct_accounts(a, b):
            return
        key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
        reasons = pairs.setdefault(key, [])
        if reason not in reasons:
            reasons.append(reason)

    by_full: Dict[str, List[Dict]] = {}
    by_first: Dict[str, List[Dict]] = {}
    by_name: Dict[str, List[Dict]] = {}
    for p in people_:
        fk = full_name_key(p["name"])
        if fk:
            by_full.setdefault(fk, []).append(p)
        toks = _tokens(p["name"])
        if p.get("birthday"):
            for t in set(toks):             # общий токен имени (имя или фамилия, порядок любой)
                by_first.setdefault(t, []).append(p)
        by_name.setdefault(" ".join(toks), []).append(p)

    for group in by_full.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                add(a, b, "full_name")
    for group in by_first.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if _same_bday(a, b):
                    add(a, b, "name_birthday")
    for a in people_:
        for alias in a.get("aliases") or []:
            ak = " ".join(_tokens(alias))
            for b in by_name.get(ak, []):
                if ak and ak != " ".join(_tokens(a["name"])):
                    add(a, b, "alias_is_name")

    by_id = {p["id"]: p for p in people_}
    out = []
    for (i, j), reasons in pairs.items():
        miss = 1.0
        for r in reasons:
            miss *= 1 - DUP_WEIGHTS[r]
        out.append({"a": by_id[i], "b": by_id[j], "score": round(1 - miss, 3), "reasons": reasons})
    out.sort(key=lambda d: (-d["score"], d["a"]["id"], d["b"]["id"]))
    return out[:limit]
