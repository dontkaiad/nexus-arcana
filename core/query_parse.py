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
_LIKE_STEM = ("любит", "любишь", "люблю", "любят", "любил", "обожа", "нравит", "нравят", "нравил", "фанат",
             "предпочита")                       # любит / любишь / люблю / нравится; НЕ «любимый», «любовь»
_EAT_STEM = ("ест", "ем", "ешь", "пью", "пьет", "пьешь", "переноси", "переношу",
             "терпит", "терплю", "выносит", "выношу", "хочет", "хочу", "курит", "курю", "употребляет")
_HATE_STEM = ("ненавиж", "ненавидит", "бесит", "раздража", "достает", "противно")
_EXACT = {"ем", "ест", "ешь", "пью"}    # короткие основы — только точное слово, остальные — префикс
# «важное» — ТОЛЬКО здоровье / запреты / явное «важно»: аллергия, нельзя, противопоказано, диагнозы
_IMPORTANT_HARD = ("аллерг", "нельзя", "противопоказ", "непереносим", "болеет", "болен", "болею", "диабет",
                   "беремен", "астм", "инвалид", "здоровь")
_IMPORTANT_SOFT = ("важн", "главное", "критичн")
_NEG_WINDOW = 3          # отрицание ищем в трёх словах ПЕРЕД глаголом: «совсем не любит», «не очень любит»

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


def _negated(w: Sequence[str], i: int) -> bool:
    return any(x in _NEG for x in w[max(0, i - _NEG_WINDOW):i])


def signals(words: Sequence[str]) -> Tuple[bool, bool, bool, bool]:
    """(отрицание_в_тексте, dislikes, likes, important) по лексикону — для запроса И для текста факта.

    dislikes — «не любит / не нравится / не ест / не пьёт / не переносит», «ненавидит / бесит / раздражает»,
    «терпеть не может»; likes — «любит / обожает / нравится / фанат» БЕЗ отрицания перед глаголом;
    important — только здоровье/запрет/«важно» (аллергия, нельзя, противопоказано, диагноз, здоровье)."""
    w = [x.replace("ё", "е") for x in words]
    neg_any = any(x in _NEG for x in w)
    dis = likes = False
    for i, x in enumerate(w):
        if _has(_HATE_STEM, [x]):
            dis = True
        elif x.startswith("терпет") and any(y in _NEG for y in w[i + 1:i + 3]):
            dis = True
        elif _has(_LIKE_STEM + _EAT_STEM, [x]):
            if _negated(w, i):
                dis = True
            elif _has(_LIKE_STEM, [x]):
                likes = True               # «ест» без любви/нелюбви — не сигнал
    imp = _has(_IMPORTANT_HARD + _IMPORTANT_SOFT, w)
    return neg_any, dis, likes, imp


def detect_types(words: Sequence[str]) -> Tuple[Tuple[str, ...], bool]:
    """Типы фактов, которые просит вопрос, и есть ли в нём отрицание.
    «не любит / ненавидит / бесит / не нравится / не ест» → ТОЛЬКО dislikes; important — только по
    «аллергия / нельзя / важно / здоровье»; обе группы в одном вопросе → оба типа."""
    neg, dis, likes, imp = signals(words)
    if imp and dis:
        return (IMPORTANT, DISLIKES), neg
    if imp:
        return (IMPORTANT,), neg
    if dis:
        return (DISLIKES,), neg
    if likes:
        return (LIKES,), neg
    return (), neg


def fact_type_from_text(text: str) -> Optional[str]:
    """Тип факта ПО ТЕКСТУ, детерминированно (без LLM). None — лексикон не сработал или факт двусмыслен
    («любит чай, но не любит кофе»): тогда тип не трогаем. Приоритет как у guess_fact_type:
    important > dislikes > likes; «не любит X» никогда не likes."""
    _neg, dis, likes, imp = signals(tokens(text))
    if imp:
        return IMPORTANT
    if dis and likes:
        return None
    if dis:
        return DISLIKES
    if likes:
        return LIKES
    return None


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
