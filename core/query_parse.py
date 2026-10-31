"""core/query_parse.py — понимание вопроса к 🧠 Памяти ДО поиска (этап 2). Без LLM, без сети.

parse(query, resolve) -> Plan: субъект (я / человек(и) / неизвестное имя / неясно),
тип факта (dislikes / important / likes), intent (facts / birthday / other).

Субъект-человек определяет единственный источник правды — `pg_people_repo.resolve`
(формы имён, падежи, отказ rejected и «Я»). Заглавные буквы НЕ используются: пишут строчными.
Парсер получает `resolve` и `name_in_facts` как функции — нет жёсткой зависимости от БД, тестируется таблицей.
Маршрутизацию (прямой запрос / гибрид) делает `core.memory_search.answer`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_WORD = re.compile(r"[\w\-]+", re.UNICODE)

# ── лексикон ────────────────────────────────────────────────────────────────

_NEG = {"не", "нет", "никогда", "ни"}
_LIKE_STEM = ("люб", "обожа", "нрав", "фанат", "предпочита")          # любит / любишь / люблю / нравится
_EAT_STEM = ("ест", "ем", "ешь", "пью", "пьет", "пьешь", "переноси", "переношу",
             "терпит", "терплю", "выносит", "выношу", "хочет", "хочу", "курит", "курю", "употребляет")
_HATE_STEM = ("ненавиж", "ненавидит", "бесит", "раздража", "достает", "противно")
_EXACT = {"ем", "ест", "ешь", "пью"}    # короткие основы — только точное слово, остальные — префикс
_IMPORTANT_HARD = ("аллерг", "нельзя", "противопоказ", "непереносим", "болеет", "болен", "болею", "диабет",
                   "беремен", "астм", "инвалид")
_IMPORTANT_SOFT = ("важн", "главное", "критичн")

_BIRTHDAY = re.compile(r"\bдр\b|день\s+рожд|д\.\s?р\.|родил(ась|ся|ись)|сколько\s+(ей|ему|ей\s+лет|лет)|"
                       r"\bвозраст|именин", re.I)

_SELF_WORDS = {"я", "мне", "меня", "мной", "мою", "мой", "моя", "мое", "мои", "моих", "моим", "себя",
               "себе", "мною"}
# глаголы 1-го лица: «не ем», «не люблю» — субъект «я» и без слова «я»
_SELF_VERBS = ("ем", "пью", "люблю", "переношу", "терплю", "выношу", "хочу", "курю", "обожаю", "ненавижу")

# слова самого вопроса — не имена
_QUERY_WORDS = {
    "что", "чего", "кто", "кого", "как", "какой", "какая", "какие", "какое", "каких", "когда", "где", "куда", "зачем",
    "почему", "сколько", "про", "о", "об", "обо", "у", "для", "к", "ко", "от", "из", "на", "в", "во", "с", "со",
    "за", "по", "и", "а", "но", "или", "ли", "бы", "же", "это", "эти", "этот", "там", "тут", "ещё", "еще", "все",
    "всё", "вообще", "именно", "помню", "знаю", "скажи", "расскажи", "напомни", "найди", "покажи", "есть", "был",
    "была", "было", "были", "будет", "ему", "ей", "его", "её", "ее", "их", "им", "ним", "ней", "него",
    "дата", "рождения", "человек", "человека", "человеку", "памяти", "память", "факты", "факт", "заметки",
    "мне", "меня", "не", "нет", "ни", "очень", "сильно", "точно", "вот",
    "др", "рождения", "родился", "родилась", "родились", "лет", "возраст", "именины",
}
_ANCHOR_BEFORE = {"про", "о", "об", "обо", "у", "для", "к", "ко", "от", "с", "со", "насчёт", "насчет"}

DISLIKES, LIKES, IMPORTANT = "dislikes", "likes", "important"


@dataclass
class Plan:
    subject: str                       # self | person | ambiguous | unknown_person | unclear
    people: List[Dict] = field(default_factory=list)   # резолвнутые люди (dict из prepo.resolve)
    unknown_name: str = ""             # для unknown_person
    types: Tuple[str, ...] = ()        # () = тип не распознан
    intent: str = "facts"              # facts | birthday
    negated: bool = False
    residual: List[str] = field(default_factory=list)  # значимые слова, не объяснённые разбором

    @property
    def structured(self) -> bool:
        """Субъект и (тип ИЛИ ДР) ясны — можно идти прямым запросом без гибрида."""
        return self.subject in ("person", "ambiguous") and bool(self.types or self.intent == "birthday")


def tokens(text: str) -> List[str]:
    t = (text or "").lower().replace("ё", "е")
    return [w.strip("-_") for w in _WORD.findall(t) if w.strip("-_")]


def _has(stems: Sequence[str], words: Sequence[str]) -> bool:
    for w in words:
        for s in stems:
            if (w == s) if s in _EXACT else w.startswith(s):
                return True
    return False


def detect_types(words: Sequence[str]) -> Tuple[Tuple[str, ...], bool]:
    """(типы по приоритету important > dislikes > likes, есть_ли_отрицание)."""
    w = [x.replace("ё", "е") for x in words]
    neg = any(x in _NEG for x in w)
    like = _has(_LIKE_STEM, w)
    eat = _has(_EAT_STEM, w)
    hate = _has(_HATE_STEM, w)
    imp_hard = _has(_IMPORTANT_HARD, w)
    imp_soft = _has(_IMPORTANT_SOFT, w)
    if imp_hard:
        return (IMPORTANT, DISLIKES), neg           # аллергия / нельзя — это и «важное», и «не ем»
    if hate or (neg and (like or eat)):
        return (IMPORTANT, DISLIKES), neg
    if imp_soft:
        return (IMPORTANT,), neg
    if like and not neg:
        return (LIKES,), neg
    return (), neg


def _is_self(words: Sequence[str]) -> bool:
    if any(w in _SELF_WORDS for w in words):
        return True
    ws = list(words)
    # «что не ем» / «не люблю»: отрицание + глагол 1-го лица
    return any(w in _SELF_VERBS for w in ws) and any(x in _NEG for x in ws)


def parse(
    query: str,
    resolve: Callable[[List[str]], List[Dict]],
    name_in_facts: Optional[Callable[[str], bool]] = None,
) -> Plan:
    words = tokens(query)
    if not words:
        return Plan(subject="unclear")
    types, neg = detect_types(words)
    intent = "birthday" if _BIRTHDAY.search(query or "") else "facts"
    if intent == "birthday":
        types = ()

    # кандидаты в имена: всё, что не слово вопроса / глагол / тип
    def _known(w: str) -> bool:
        return (w in _QUERY_WORDS or w in _SELF_WORDS or w in _NEG
                or _has(_LIKE_STEM + _EAT_STEM + _HATE_STEM + _IMPORTANT_HARD + _IMPORTANT_SOFT, [w])
                or w in _SELF_VERBS or len(w) < 2)

    cand = [w for w in words if not _known(w)]

    people: List[Dict] = resolve(cand) if cand else []
    self_marked = _is_self(words)

    if people and self_marked:
        # «что я люблю из того, что любит X» — смешанный субъект: не гадаем
        return Plan(subject="unclear", people=people, types=types, intent=intent, negated=neg, residual=cand)
    if people:
        # слова, не объяснённые резолвом (остаток) — информация для логов
        return Plan(subject="person" if len(people) == 1 else "ambiguous", people=people, types=types,
                    intent=intent, negated=neg, residual=[])
    if self_marked:
        return Plan(subject="self", types=types, intent=intent, negated=neg, residual=cand)

    # имя без совпадений: только при ЯКОРЕ («про X», «что любит X», «у X ...») и если слова нет в фактах
    if len(cand) == 1 and (types or intent == "birthday"):
        name = cand[0]
        if _anchored(words, name) and not (name_in_facts and name_in_facts(name)):
            return Plan(subject="unknown_person", unknown_name=name, types=types, intent=intent,
                        negated=neg, residual=[])
    return Plan(subject="unclear", types=types, intent=intent, negated=neg, residual=cand)


def _anchored(words: Sequence[str], name: str) -> bool:
    """Слово стоит там, где ожидается субъект: после предлога («про X», «у X») либо как последнее /
    единственное значимое слово в «что (не) VERB X» и «что X (не) VERB»."""
    i = words.index(name)
    if i > 0 and words[i - 1] in _ANCHOR_BEFORE:
        return True
    rest = [w for w in words if w != name]
    return len(rest) >= 1 and (i == len(words) - 1 or i == 1)
