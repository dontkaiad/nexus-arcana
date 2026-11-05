"""core/memory_subjects.py — разделы Памяти по субъекту и категории фактов «Я» (чистые функции; БД трогает только load_cat_names).

Раздел выводится, а не хранится: Я = person.is_self, Люди = person_id задан, Коты = категория 🐾 Коты,
Разное = всё остальное; бюджетные категории — системные и в разделы не входят.
Здесь же правила разметки: старые памятей без человека → субъект (`legacy_target`) и факты «Я» → категория
(`keyword_category`; остаток размечает Haiku из scripts/assign_memory_subjects.py).
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

# Категории «Я». 🐾 Коты — целевая категория для кошачьего (раздел «Коты»), факт остаётся привязан к карточке «Я».
SELF_CATEGORIES: List[str] = [
    "🦋 СДВГ", "🏥 Здоровье", "🛒 Предпочтения", "💼 Работа", "🔮 Практика", "🏠 Быт", "🔄 Паттерн",
    "🪞 Внешность", "💞 Отношения", "🎮 Хобби", "🐾 Коты", "🗂 Прочее", "❓ Проверить автора",
]
REVIEW_AUTHOR = "❓ Проверить автора"         # факт мог быть написан не про Кай (мужской маркер, чат не сопоставлен)
FALLBACK_CATEGORY = "🗂 Прочее"
CATS_CATEGORY = "🐾 Коты"
# Бюджет и прочая системная память — не субъект, в разделы Памяти не попадает.
SYSTEM_CATEGORIES = {"💰 Лимит", "🔒 Постоянные", "📥 Доход", "📋 Долги", "🎯 Цели", "🎭 Фигуранты", "Настройки"}
SYSTEM_KEY_PREFIXES = ("income_", "постоянно_", "разовый_", "лимит_", "долг_", "цель_")
# Категории старых памятей, которые по смыслу «про Кай».
SELF_LEGACY = {"🦋 СДВГ", "🏥 Здоровье", "🛒 Предпочтения", "💼 Работа", "🔄 Паттерн", "💡 Инсайт", "🔮 Практика"}

_I = re.I
# Имена и клички котов в коде не хранятся: их даёт таблица `cats` (load_cat_names → keyword_category(cat_names=…)).
# Имя — с заглавной (регистр важен: «тучка» — это ещё и облачко); кличка — только как подлежащее в начале факта и
# не часть названия («Тучка Юниверс»: следом идёт слово с заглавной).
@lru_cache(maxsize=32)
def _cat_names_re(names: Tuple[str, ...]):
    if not names:
        return None
    return re.compile(r"^\W*(?:%s)(?!\w)(?!\s+[А-ЯЁA-Z])" % "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))


def load_cat_names(user_id: str) -> List[str]:
    """Имена и клички котов владельца из таблицы cats. Нет БД, нет таблицы, нет котов — пустой список, без падения."""
    try:
        from core.repos import pg_cats_repo
        return pg_cats_repo.cat_names(user_id)
    except Exception:
        return []


_CAT_WORDS = re.compile(r"\bкот(?:а|у|ом|е|ы|ов|ам|ик\w*|ёнок|ёнк\w*)?\b|кошк|кошеч|котят|лоток|\bкорм(?:а|у|ом)?\b для (?:кош|кот)",
                        _I)
_PSYCH = re.compile(r"\bман(?:ия|ии|ией|ию|иакальн\w*)\b|маниак|биполяр|психиатр|психоз|шизо|суицид|самоповрежд|"
                    r"расстройств|паническ|депресс(?!ивн)|тревожн", _I)
_LIKES_CTX = re.compile(r"любит|обожа|нравит|нравил|любимый|любимая", _I)
_ESO_CTX = re.compile(r"таро|расклад|эзотер|ведьм|магическ|магия|свеч|алтар|гримуар|колод|оракул|руны|астролог|обряд", _I)
_RULES = [
    ("💞 Отношения", r"статус отношени|бывш|отношени|\bпарн(?:я|ю|ем|е)\b|\bпарень\b|\bмуж(?:а|у|ем|е)?\b|\bжен[аыу]\b|девушк|родител|"
                      r"\bмам[аыуе]\b|\bмать\b|\bматер[ьи]\b|\bотец\b|\bотц[аеу]\b|\bпап[аыуе]\b|\bбрат(?:а|у|ом|е)?\b|"
                      r"\bсестр\w*|семь(?:я|и|ю|е)\b|\bдруг(?:а|у|ом|е|и|ей|ов)?\b|друзь|подруг|подруж|развод|свидани|"
                      r"влюбл|расстал|ревн|ссор[аыуе]"),
    ("🪞 Внешность", r"\bтату|татуиров|волос|обхват|причёск|стрижк|пирсинг|шрам|родинк|веснушк|рост \d|\d+ ?см\b"),
    ("🦋 СДВГ", r"сдвг|дефицит внимани|гиперфокус|прокрастин|не могу сосредоточ|концентрац|дофамин|импульсивн|рассеян"),
    ("🏥 Здоровье", r"аллерги|болит|здоровь|врач|лор-|таблетк|лекарств|диагноз|давлени|мигрен|бессонн|не сплю|тошнит|"
                    r"непереносим|терапевт|психотерап|месячн|простуд|температур|болезн|болею|больниц|операци|"
                    r"зуб[аыу]?\b|антидепр|витамин"),
    ("💼 Работа", r"работ[аыуе]\b|работает|коллег|начальни|зарплат|проект|офис|фриланс|карьер|заказчик|собеседован|"
                  r"увольн|должност|бизнес|самозанят"),
    ("🛒 Предпочтения", r"любит|любл|нравит|предпочита|обожа|ненавид|терпеть не|бесит|раздражает|любимый|любимая|"
                        r"не ем\b|не пью\b|кофе|\bчай\b|музык|фильм|сериал"),
    ("🏠 Быт", r"квартир|живёт|живет|живу|переезд|уборк|стирк|ремонт|мебел|посуд"),
    ("🔄 Паттерн", r"всегда|обычно|привыч|склонн|каждый раз"),
]
_COMPILED = [(c, re.compile(rx, _I)) for c, rx in _RULES]
_PRACTICE_STRICT = re.compile(r"таро|расклад|астролог|\bруны\b|\bрун(?:ы|ах|ическ\w*)|ведьм|оракул|гримуар|эзотер|натальн|гадани|магия|магии|магическ", _I)
_HOBBY = re.compile(r"\bигр(?:а|ы|у|е|ой)\b|игра(?:ет|ть|ю)\s+в\s|\blol\b|league of|лига легенд|косплей|cosplay|рисова|рисует|фанфик|"
                    r"\bманг[аиуе]\b|аниме|фандом|аркейн|геймер|сериал|мультик|мультфильм", _I)
_TACTILE = re.compile(r"тактильн|трога|касан|касат|глад|перебира|щупа|на ощупь|прикосновен|мягк|шелков", _I)


def keyword_category(fact: str, cat_names: Sequence[str] = ()) -> Optional[str]:
    """Категория «Я» по ключевым словам или None (тогда решает Haiku). Порядок важен: первый сработавший.
    `cat_names` — имена и клички котов владельца (load_cat_names); без них кот определяется только по словам «кот», «лоток»…
    Коты (слово или кличка в начале) → психиатрия/мании (Здоровье, не СДВГ) → хобби → практика (только эзотерика;
    «ритуал» сам по себе ничего не значит) → отношения → внешность → СДВГ → здоровье → работа → предпочтения → быт → паттерн.
    «депрессивный» в «любит/обожает …» — вкус, не здоровье."""
    raw = fact or ""
    t = raw.lower()
    names_re = _cat_names_re(tuple(cat_names)) if cat_names else None
    if (names_re and names_re.search(raw)) or _CAT_WORDS.search(t):
        return CATS_CATEGORY
    if _PSYCH.search(t) and not (re.search(r"депресс", t) and _LIKES_CTX.search(t) and not re.search(r"мани|психиатр|диагноз|лекарств", t)):
        return "🏥 Здоровье"
    if _HOBBY.search(t) and not _PRACTICE_STRICT.search(t):
        return "🎮 Хобби"
    if _PRACTICE_STRICT.search(t) or ("ритуал" in t and _ESO_CTX.search(t)):
        return "🔮 Практика"
    for cat, rx in _COMPILED:
        if rx.search(t):
            if cat == "🪞 Внешность" and _TACTILE.search(t):
                return "🛒 Предпочтения"          # «любит гладить волосы» — вкус, а не внешность
            return cat
    return None


# ── Атрибуция по ЦИТАТЕ: чей это факт (quote_author + лицо цитаты), а не род в тексте факта ─────────────────
# Текст факта Haiku пересказал сам (часто в мужском роде), поэтому по тексту автора не определить; цитата — слова
# из переписки как есть, и quote_author говорит, кто их написал.
_S1 = re.compile(r"\b(?:я|мы)\b", re.I)                      # подлежащее 1 л.
_S2 = re.compile(r"\b(?:ты|вы)\b", re.I)                      # подлежащее 2 л.
_O1 = re.compile(r"\b(?:мне|меня|мной|мною|мо[йяеи]|моё|мое|моих|моим|моими|мою|моего|моему|моей|нам|нас|наш[аеиу]?)\b", re.I)
_O2 = re.compile(r"\b(?:тебе|тебя|тобой|тобою|тв[оо]й|твоя|твоё|твое|твои|твоих|твоим|твою|твоего|твоей|вам|вас)\b", re.I)
_FIRST_PRON = re.compile(_S1.pattern + "|" + _O1.pattern, re.I)
_SECOND_PRON = re.compile(_S2.pattern + "|" + _O2.pattern, re.I)
_OTHER_SUBJ = re.compile(r"\b(?:он|она|они|оно|его|её|ее|ему|ей|им|их|ним|нем|ней|нее|неё|них|ними|нему)\b", re.I)
_INTERLOC = re.compile(r"собеседник", re.I)
_OTHER_NAME = re.compile(r"(?<![\.!?]\s)(?<!^)\b[А-ЯЁ][а-яё]{2,}\b|@\w+")      # имя с заглавной в середине фразы или @ник
# Глагол 1 л. ед.ч. (наст./буд.): -ю/-юсь, -усь, -жу/-шу/-чу/-щу, несколько неправильных; прош. вр. в любом роде (-л/-ла/-лся/-лась).
# Род НЕ используется: Кай пишет о себе и в м.р., и в ж.р.
_NOT_VERB_YU = {"семью", "неделю", "землю", "тётю", "тетю", "статью", "жизнью", "ночью", "дочь", "кухню", "мою", "твою", "свою", "нашу"}
_V1_PRES = re.compile(r"^(?:[а-яё]{2,}(?:ю|юсь|усь)|[а-яё]+[жшчщ]у|съем|хочу|могу|ем|пью|жду|иду|еду|буду|пойду|найду|смогу|лягу|встану|"
                      r"знаю|умею|терплю|люблю|ненавижу)$", re.I)
_V_PAST = re.compile(r"^[а-яё]{1,}(?:[аяеиоуы]л|[аяеиоуы]ла|[аяеиоуы]ло|[аяеиоуы]ли|[аяеиоуы]лся|[аяеиоуы]лась|[аяеиоуы]лись)$", re.I)
_NOT_PAST = {"стол", "угол", "осел", "козел", "козёл", "орел", "орёл", "котёл", "котел", "посол", "дол", "ствол", "подол", "пол", "укол",
             "завод", "когда", "была", "было", "были", "было", "мало", "мыла", "зола", "школа", "метла", "скала", "ветла"}
_V2 = re.compile(r"^[а-яё]{2,}(?:ешь|ёшь|ишь)(?:ся)?$", re.I)
_WORD = re.compile(r"[а-яёА-ЯЁ]+")


def _words(q):
    return [w.lower() for w in _WORD.findall(q or "")]


def _verb1(q):
    other = bool(_OTHER_SUBJ.search(q or "") or _SECOND_PRON.search(q or ""))   # прош. вр. — только без другого подлежащего
    for w in _words(q):
        if w in _NOT_VERB_YU or w in _NOT_PAST:
            continue
        if w in ("ем", "пью", "жду", "иду", "еду"):
            return True
        if len(w) >= 3 and (_V1_PRES.match(w) or (not other and _V_PAST.match(w))):
            return True
    return False


def _verb2(q):
    return any(len(w) >= 5 and _V2.match(w) for w in _words(q))


def person_of(q: str):
    """Лицо, о котором говорит цитата: 'first' | 'second' | 'both' | None. Сначала подлежащее (я/ты, глагол), потом косвенные формы."""
    s1, s2 = bool(_S1.search(q or "")) or _verb1(q), bool(_S2.search(q or "")) or _verb2(q)
    if not (s1 or s2):
        s1, s2 = bool(_O1.search(q or "")), bool(_O2.search(q or ""))
    return "both" if s1 and s2 else "first" if s1 else "second" if s2 else None


def has_first(q: str) -> bool:
    return person_of(q) in ("first", "both")


def has_second(q: str) -> bool:
    return person_of(q) in ("second", "both")


def _stem_set(t):
    return {w[:5] for w in _words(t) if len(w) >= 4}


def _clause_person(fact: str, quote: str):
    """Оба лица в цитате: берём клаузу, лучше всего совпавшую с фактом по основам слов, и смотрим лицо в ней.
    → 'first' | 'second' | None (неясно)."""
    # сначала сказуемое: слово цитаты с той же основой, что глагол факта («мечтает» ↔ «мечтаю») — его лицо и решает
    for fw in _words(fact):
        if len(fw) < 4:
            continue
        for qw in _words(quote):
            if qw[:3] == fw[:3] and len(qw) >= 3:
                if _V2.match(qw):
                    return "second"
                if _V1_PRES.match(qw) or _V_PAST.match(qw):
                    return "first"
    fs = _stem_set(fact)
    best, score = None, 0
    for cl in re.split(r"[.!?;,\n]| и | а | но ", quote or ""):
        sc = len(fs & _stem_set(cl))
        if sc > score:
            best, score = cl, sc
    if not best:
        return None
    p = person_of(best)
    return p if p in ("first", "second") else None


def attribution(fact: str, quote: str, quote_author: str):
    """(вердикт, причина) для факта карточки «Я». Решает ЦИТАТА (кто сказал + о ком), текст факта и род не используются.
    interloc      — «Собеседник» в тексте факта: смысл перевёрнут → rejected;
    keep          — факт про Кай: kai + 1-е лицо, либо partner + 2-е лицо («ты работаешь» — обо мне со слов собеседника);
    to_chat       — факт про собеседника: partner + 1-е лицо, либо kai + 2-е лицо («ты любишь…») → на человека этого чата;
    check         — не ясно → «❓ Проверить автора».
    Если в цитате оба лица — по клаузе, где сказуемое факта; не нашли — check."""
    if _INTERLOC.search(fact or ""):
        return "interloc", "собеседник в тексте"
    q = quote or ""
    pq = person_of(q)
    first, second = pq in ("first", "both"), pq in ("second", "both")
    author = (quote_author or "").lower()
    if author not in ("kai", "partner"):
        return "check", f"author_{author or 'unknown'}"
    if first and second:
        who = _clause_person(fact, q)
        if who is None:
            return "check", f"{author}_both_unclear"
        first, second = who == "first", who == "second"
    if not first and not second:
        if author == "kai" and q.strip() and not _OTHER_SUBJ.search(q) and not _OTHER_NAME.search(q):
            return "keep", "kai_elided"          # обрывок без подлежащего и без других людей: Кай написала сама, значит про себя
        return "check", f"{author}_no_marker"
    about_me = (author == "kai") == first            # kai+1л или partner+2л
    if about_me:
        return "keep", "kai_first" if author == "kai" else "partner_about_me"
    return "to_chat", "kai_about_other" if author == "kai" else "partner_first"


# ── Временные состояния: кандидаты на отклонение (не категория, не применяется автоматически) ──

_TEMP = re.compile(r"сейчас|в данный момент|на данный момент|пока что|находится|переезжает|собирается|ожидает|ждёт|ждет|"
                   r"сегодня|завтра|вчера|на этой неделе|в холодильнике|в стирке|на зарядке|в пути|едет|в процессе", _I)


def temp_signal(fact: str) -> bool:
    return bool(_TEMP.search(fact or ""))


def is_system(category: str, key_name: str) -> bool:
    return (category or "") in SYSTEM_CATEGORIES or (key_name or "").startswith(SYSTEM_KEY_PREFIXES)


def legacy_target(category: str, key_name: str, person_ids: Sequence[int]) -> str:
    """Куда относится старая память без человека. `person_ids` — кого нашло по related_to (резолв делает вызывающий).
    Возврат: 'system' | 'self' | 'cats' | 'person' | 'misc'. Неоднозначность (тёзки) → misc, без догадок."""
    if is_system(category, key_name):
        return "system"
    if category in SELF_LEGACY:
        return "self"
    if category == CATS_CATEGORY:
        return "cats"
    if category == "👥 Люди":
        return "person" if len(set(person_ids)) == 1 else "misc"
    return "misc"


# ── Haiku-пакеты для остатка ────────────────────────────────────────────────

CATEGORIZE_SYSTEM = (
    "Ты раскладываешь короткие факты о ЧЕЛОВЕКЕ по одной категории. Категории:\n"
    "СДВГ — ТОЛЬКО особенности внимания, фокуса, памяти, откладывания; манию, психиатрию, диагнозы — НЕ сюда\n"
    "Здоровье — самочувствие, болезни, аллергии, лекарства, сон, психиатрия, мании, питание по здоровью\n"
    "Предпочтения — что любит / не любит, вкусы, еда, музыка, фильмы, игры, покупки\n"
    "Работа — работа, учёба, проекты, деньги от работы, профессиональные навыки\n"
    "Практика — ТОЛЬКО эзотерика: таро, астрология, руны, магия (не хобби и не работа)\n"
    "Быт — жильё, вещи, транспорт, повседневные дела\n"
    "Паттерн — повторяющиеся модели поведения и реакции, не связанные с СДВГ\n"
    "Внешность — тело, волосы, тату, одежда-стиль, рост/вес\n"
    "Хобби — игры (LoL и др.), косплей, рисование, фанфики, манга/аниме, сериалы и фандомы\n"
    "Отношения — партнёры, бывшие, родители, семья, друзья, как строит отношения\n"
    "Коты — коты и кошки (имена, привычки, корм, уход)\n"
    "Прочее — если ни одна не подходит (биография, планы)\n"
    "Вход: строки «id|факт». Ответ: ТОЛЬКО JSON-объект {\"id\":\"категория\"} — по ключу на каждую строку, "
    "название категории из списка выше (можно без эмодзи). Без пояснений."
)


def _canonical(v) -> str:
    """Название категории → каноническая строка с эмодзи. Модель то пишет «🏥 Здоровье», то просто «Здоровье»:
    сравниваем по буквам названия. Неизвестное → 🗂 Прочее (чужое не пишем)."""
    if not isinstance(v, str):
        return FALLBACK_CATEGORY
    name = re.sub(r"[^\w]+", "", v, flags=re.U).lower()
    for cat in SELF_CATEGORIES:
        if re.sub(r"[^\w]+", "", cat, flags=re.U).lower() == name:
            return cat
    return FALLBACK_CATEGORY


def parse_categorize_reply(raw: str, ids: Sequence[int]) -> Dict[int, str]:
    """JSON-ответ Haiku → {id: категория}. Неизвестная/пропущенная категория → 🗂 Прочее (молча чужое не пишем)."""
    import json
    m = re.search(r"\{.*\}", raw or "", re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = {}
    out: Dict[int, str] = {}
    for i in ids:
        out[i] = _canonical(data.get(str(i)))
        if out[i] == REVIEW_AUTHOR:                                  # служебная категория — не для модели
            out[i] = FALLBACK_CATEGORY
    return out
