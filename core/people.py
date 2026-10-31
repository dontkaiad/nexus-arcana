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
DUP_WEIGHTS = {"full_name": 0.7, "name_birthday": 0.6, "alias_is_name": 0.5, "shared_names": 0.5}
DUP_LIMIT = 200


def _norm(s: str) -> str:
    return _re.sub(r"[^\w\s@]", " ", (s or "").lower().replace("ё", "е"), flags=_re.UNICODE).strip()


def _tokens(s: str) -> List[str]:
    return [t for t in _norm(s).split() if t]


def alias_key(s: str) -> str:
    """Ключ имени/алиаса для сравнения «у двоих одно и то же имя»: слова в нижнем регистре, падежное окончание
    у слов ≥4 букв срезано («Вадима» == «Вадим»). Короткие слова сравниваются как есть."""
    return " ".join(_strip(w) if len(w) >= 4 else w for w in _tokens(s))


def alias_disputes(rows: Iterable[Dict[str, Any]], reviewed: Iterable[Tuple[int, int, str]] = ()) -> Dict[int, List[Dict[str, Any]]]:
    """Спорные «Другие имена»: алиас человека целиком совпадает с именем или алиасом ДРУГОГО человека
    (в том числе «Я»). {person_id: [{"alias", "also": [{"id","name","is_self"}]}]}; отклонённые не участвуют,
    свои алиасы «Я» не проверяются, алиас = собственное имя — не спор.
    `reviewed` — пары «Тёзки, ок» (меньший id, больший id, alias_key): такое совпадение не спорное; новый
    человек с тем же именем в пару не входит и снова подсвечивается."""
    ok = set(reviewed)
    people_ = [r for r in rows if r.get("status") != "rejected"]
    owners: Dict[str, List[Dict[str, Any]]] = {}
    for p in people_:
        keys = {alias_key(p["name"])} | {alias_key(a) for a in p.get("aliases") or []}
        if p.get("is_self"):                  # «Я» откликается и на отдельное слово имени («Кай» для «Кай Тестова»)
            keys |= {alias_key(w) for w in _tokens(p["name"])}
        for k in keys:
            if k:
                owners.setdefault(k, []).append(p)
    out: Dict[int, List[Dict[str, Any]]] = {}
    for x in people_:
        if x.get("is_self"):
            continue
        own = alias_key(x["name"])
        for alias in x.get("aliases") or []:
            k = alias_key(alias)
            if not k or k == own:
                continue
            also = [{"id": y["id"], "name": y["name"], "is_self": bool(y.get("is_self"))}
                    for y in owners.get(k, []) if y["id"] != x["id"]
                    and (min(x["id"], y["id"]), max(x["id"], y["id"]), k) not in ok]
            if also:
                out.setdefault(x["id"], []).append({"alias": alias, "also": also})
    return out


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


def find_duplicates(rows: Iterable[Dict[str, Any]], limit: int = DUP_LIMIT) -> List[Dict[str, Any]]:
    """Пары-кандидаты в один человек. НИЧЕГО не склеивает — только подсказка для merge.

    Сигналы: совпала нормализованная пара имя+фамилия; общий токен имени и одинаковый ДР;
    алиас одного равен имени другого. Отклонённых (rejected) пропускаем."""
    people_ = [r for r in rows if r.get("status") != "rejected" and not r.get("is_self")]
    pairs: Dict[Tuple[int, int], List[str]] = {}

    def add(a, b, reason):
        if a["id"] == b["id"]:
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
    # «Популярное» имя несут многие (имя-алиас у десятков людей) — это не сигнал дубля:
    # засчитываем алиас=имя только если ключ принадлежит не более чем двум людям.
    carriers: Dict[str, set] = {}
    for p in people_:
        for k in {" ".join(_tokens(p["name"]))} | {" ".join(_tokens(x)) for x in p.get("aliases") or []}:
            if k:
                carriers.setdefault(k, set()).add(p["id"])
    for a in people_:
        for alias in a.get("aliases") or []:
            ak = " ".join(_tokens(alias))
            if not ak or len(carriers.get(ak, ())) > 2:
                continue
            for b in by_name.get(ak, []):
                if ak != " ".join(_tokens(a["name"])):
                    add(a, b, "alias_is_name")

    # «общие имена»: взаимно — алиас каждого равен имени/алиасу другого (дубль или остаток склейки)
    ident = {p["id"]: ({alias_key(p["name"])} | {alias_key(a) for a in p.get("aliases") or []}) - {""} for p in people_}
    own_al = {p["id"]: {alias_key(a) for a in p.get("aliases") or []} - {""} for p in people_}
    carriers_k: Dict[str, List[Dict]] = {}
    for p in people_:
        for k in ident[p["id"]]:
            carriers_k.setdefault(k, []).append(p)
    # популярное имя (у троих и более) — не сигнал, как и в alias_is_name
    rare_al = {i: {k for k in ks if len(carriers_k.get(k, ())) <= 2} for i, ks in own_al.items()}
    for a in people_:
        for k in rare_al[a["id"]]:
            for b in carriers_k.get(k, []):
                if b["id"] > a["id"] and rare_al[b["id"]] & ident[a["id"]]:
                    add(a, b, "shared_names")

    by_id = {p["id"]: p for p in people_}
    out = []
    for (i, j), reasons in pairs.items():
        miss = 1.0
        for r in reasons:
            miss *= 1 - DUP_WEIGHTS[r]
        out.append({"a": by_id[i], "b": by_id[j], "score": round(1 - miss, 3), "reasons": reasons})
    out.sort(key=lambda d: (-d["score"], d["a"]["id"], d["b"]["id"]))
    return out[:limit]


# ── факты: ключ и текст (общие для импорта и ручного ввода) ─────────────────

import hashlib as _hashlib


def fact_hash(fact: str) -> str:
    """hash8 от нормализованного факта (lower, strip, схлопнутые пробелы)."""
    return _hashlib.sha1(_re.sub(r"\s+", " ", fact.lower().strip()).encode("utf-8")).hexdigest()[:8]


_KEY_RE = _re.compile(r"^(person_\d+_)([a-z]+)(_[0-9a-f]{8})$")
FACT_TYPES = ("important", "likes", "dislikes", "other")


def retype_key(key: str, new_type: str) -> Optional[str]:
    """Ключ факта человека person_<id>_<type>_<hash8> с другим типом. None — ключ не такого вида."""
    m = _KEY_RE.match(key or "")
    return "%s%s%s" % (m.group(1), new_type, m.group(3)) if m and new_type in FACT_TYPES else None


def fact_text(name: str, fact: str) -> str:
    """Факт без имени бесполезен в контексте бота («любит кофе» — кто?). Если имени
    (по первым 4 буквам первого слова) в факте нет — ставим префикс."""
    first = (name.split() or [""])[0]
    stem = _norm(first)[:4]
    return fact if not stem or stem in _norm(fact) else "%s: %s" % (first, fact)


_FT_IMPORTANT = _re.compile(r"аллерги|нельзя|противопоказ|непереносим|болеет|диабет|беремен|важно", _re.I)
_FT_DISLIKES = _re.compile(r"\bне\s+(любит|ест|пьёт|пьет|терпит|переносит|выносит)|ненавидит|бесит|раздража", _re.I)
_FT_LIKES = _re.compile(r"\b(любит|обожает|нравится|фанат)", _re.I)


def guess_fact_type(fact: str) -> str:
    """Тип факта для ручной записи из чата («запомни, что Оля любит чай»): important | dislikes | likes | other.
    Регулярки, без LLM; ошибиться не страшно — тип правится в карточке."""
    if _FT_IMPORTANT.search(fact or ""):
        return "important"
    if _FT_DISLIKES.search(fact or ""):
        return "dislikes"
    if _FT_LIKES.search(fact or ""):
        return "likes"
    return "other"


def owner_fact(fact: str, related_to: str = "", person_id=None) -> str:
    """Текст факта для чтения ботами. Факты людей хранятся БЕЗ префикса «Имя: » — имя берём из `related_to`
    («Имя | другое имя | …»), как и в карточке. Не факт человека или имя уже есть в тексте — без изменений.
    Старый префикс «Другое имя: » (имя/алиас этой карточки, в том числе пришедший со склеенной карточкой)
    срезается, а потом ставится основное имя — иначе в ответе «Черепашка: …» у карточки «Ольга»."""
    if not person_id:
        return fact
    names = [n.strip() for n in (related_to or "").split(" | ") if n.strip()]
    if not names:
        return fact
    return fact_text(names[0], strip_name_prefix(fact, names[0], names[1:]))


def related_to_str(name: str, aliases: Iterable[str]) -> str:
    return " | ".join([name] + [a for a in aliases if a])


def strip_name_prefix(fact: str, name: str, aliases: Iterable[str] = ()) -> str:
    """Убирает префикс «Имя: » из текста факта ВНУТРИ карточки этого человека. Префикс ставит
    `fact_text()` при импорте/ручном вводе ради контекста бота («любит кофе» — чей?), а в самой карточке
    он лишний. Срезаем только если слово перед двоеточием — слово из имени или другого имени человека
    (так «Важно: …» не пострадает)."""
    m = _re.match(r"^\s*([^\s:]{1,40}):\s+(.+)$", fact or "", _re.S)
    if not m:
        return fact
    known = set()
    for src in [name] + list(aliases or []):
        known.update(_tokens(src))
    return m.group(2) if _norm(m.group(1)) in known else fact


# ── нормализация имён/алиасов для резолва упоминаний (PR-3) ─────────────────
# people.aliases_norm = нормализованные формы имени+алиасов; упоминание из текста
# («Маше», «с Машей») приводится к тем же формам и ищется оператором && (GIN).
# Правила:
#   • слово режется через strip_case_ending (основа ≥ 3 букв);
#   • КОРОТКОЕ имя (основа < 3 букв: «Оля», «Ася», «Ян») — только точная форма, она
#     хранится с префиксом "=" («=оля»): «Оле»/«Олей» её не находят — слишком много
#     ложных совпадений;
#   • @username — как есть;
#   • для многословного имени добавляется и форма фразы целиком, и форма каждого слова.

from core.ru_morph import _CASE_SUFFIXES, strip_case_ending as _strip

_WORD = _re.compile(r"@?[\w\-]+", _re.UNICODE)


def _words(text: str) -> List[str]:
    t = (text or "").lower().replace("ё", "е")
    return [w.strip("-_") for w in _WORD.findall(t) if len(w.strip("-_")) >= 2]


def _is_short(word: str) -> bool:
    if len(word) < 3:
        return True
    return _strip(word) == word and any(word.endswith(s) and len(word) - len(s) < 3 for s in _CASE_SUFFIXES)


def _short_declensions(word: str) -> List[str]:
    """Падежные формы КОРОТКОГО имени, перечисленные явно («Оля» → Оле/Олей/Олю/Оли…): точный матч по списку,
    а не по основе — «Олег»/«Олеся» не цепляются."""
    if word[-1] in "ая":
        st, ya = word[:-1], word[-1] == "я"
        return [st + x for x in ("е", "и", "ю" if ya else "у", "ей" if ya else "ой", "ею" if ya else "ою")]
    if word[-1] == "ь":
        return [word[:-1] + x for x in ("я", "ю", "ем", "е")]
    if word[-1] in "аеёиоуыэюя":
        return []
    return [word + x for x in ("а", "у", "ом", "е")]


def _forms(word: str, expand: bool) -> List[str]:
    if word.startswith("@"):
        return [word]
    if not _is_short(word):
        return [_strip(word)]
    out = ["=" + word]
    if expand:                                   # только для хранимых имён; упоминание ищется как есть
        out += ["=" + f for f in _short_declensions(word)]
    return out


def norm_forms(text: str, expand: bool = False) -> List[str]:
    """Формы одного имени/алиаса/упоминания (без дублей, порядок стабилен)."""
    words = _words(text)
    out: List[str] = []
    if len(words) > 1:
        out.append(" ".join(_forms(w, False)[0] for w in words))
    for w in words:
        for f in _forms(w, expand):
            if f not in out:
                out.append(f)
    return out


def alias_forms(alias: str, phrase_only: bool = True) -> List[str]:
    """Формы ОДНОГО «другого имени». Однословный алиас — как имя (слово + падежные формы). Многословный
    («муж Оли», «сестра Маши», «Маша с работы») — только фраза целиком: слова по отдельности не
    индексируются, иначе описание «муж Оли» откликалось бы на «Оля» и плодило ложных тёзок.
    phrase_only=False — прежнее поведение (нужно только для отката миграции)."""
    words = _words(alias)
    if len(words) > 1 and phrase_only:
        return [" ".join(_forms(w, False)[0] for w in words)]
    return norm_forms(alias, expand=True)


def aliases_norm_for(name: str, aliases: Iterable[str], phrase_only: bool = True) -> List[str]:
    """Формы имени и «Других имён» (username'ы аккаунтов сюда не входят: тема PR-7 — отдельная таблица).
    Имя из нескольких слов ищется и по отдельным словам («Миша Ари» ← «Миша»); многословные алиасы — только
    фразой целиком (см. alias_forms)."""
    seen: List[str] = []
    for f in norm_forms(name, expand=True):
        if f not in seen:
            seen.append(f)
    for a in aliases or []:
        for f in alias_forms(a, phrase_only):
            if f not in seen:
                seen.append(f)
    return seen


def birthday_context(birthday: Optional[date], year_known: bool = True) -> str:
    """«ДР: 05.03» (+ «.1990», только если год известен) для контекста бота; пусто без ДР."""
    if not birthday:
        return ""
    known = year_known and birthday.year != UNKNOWN_YEAR
    return "ДР: %02d.%02d%s" % (birthday.day, birthday.month, (".%d" % birthday.year) if known else "")


def mention_candidates(mentions: Iterable[str]) -> List[str]:
    """Формы упоминаний для поиска по aliases_norm (&&). Одиночные слова и фраза целиком."""
    seen: List[str] = []
    for m in mentions or []:
        for f in norm_forms(m):
            if f not in seen:
                seen.append(f)
    return seen
