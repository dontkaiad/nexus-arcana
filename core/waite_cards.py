"""core/waite_cards.py — закрытый словарь 78 карт Уэйта + детерминированный разбор.

ТОЛЬКО для колоды Уэйт (rider-waite). Авторские колоды (Dark Wood, Deviant Moon,
Ленорман, игральные) сюда НЕ заходят — у них свой путь (grounding + tarot_refs).

Зачем отдельный жёсткий парсер (а не grounding-угадайка SequenceMatcher):
у Уэйта структура карты предельно регулярна — РАНГ + МАСТЬ (56 младших) или имя
старшего аркана (22). Это раскладывается детерминированно, БЕЗ порогов похожести
и БЕЗ зависимости от Haiku-спелла (который недетерминированно переписывал мисхёрд
«крыльева мячей» → валидную-но-ЧУЖУЮ «король жезлов» ещё ДО парсера). Здесь
«крыльева мячей» → Queen of Swords считается железно по alias-словарю.

Гибрид: сначала детерминированный код (бесплатно), и только на НЕЗНАКОМОМ мисхёрде,
который словарь не разобрал, — узкий Haiku-классификатор «фраза → одна из 78 | null»
(см. :func:`classify_waite_card_llm`). Результат ОБЯЗАН быть членом 78 или None.

Канонический выход — АНГЛИЙСКИЙ: «Seven of Pentacles», «Queen of Swords».

Единый источник истины по составу 78 — он же реестр miniapp `deck_cards.json`
(rider-waite). Совпадение составов стережёт tests/test_waite_cards.py (как
test_models_audit стережёт роутинг моделей).
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger("core.waite_cards")


# ── Закрытый словарь 78 (hardcode) ───────────────────────────────────────────

# 22 старших аркана: canonical EN ← каноничный RU. Порядок как в реестре.
_MAJORS: List[Tuple[str, str]] = [
    ("The Fool", "Шут"),
    ("The Magician", "Маг"),
    ("The High Priestess", "Жрица"),
    ("The Empress", "Императрица"),
    ("The Emperor", "Император"),
    ("The Hierophant", "Иерофант"),
    ("The Lovers", "Влюблённые"),
    ("The Chariot", "Колесница"),
    ("Strength", "Сила"),
    ("The Hermit", "Отшельник"),
    ("Wheel of Fortune", "Колесо Фортуны"),
    ("Justice", "Справедливость"),
    ("The Hanged Man", "Повешенный"),
    ("Death", "Смерть"),
    ("Temperance", "Умеренность"),
    ("The Devil", "Дьявол"),
    ("The Tower", "Башня"),
    ("The Star", "Звезда"),
    ("The Moon", "Луна"),
    ("The Sun", "Солнце"),
    ("Judgement", "Суд"),
    ("The World", "Мир"),
]

# 56 младших = 4 масти × 14 рангов. canonical EN = «{Rank} of {Suit}».
# Порядок (масть внешняя, ранг внутренний) — как в реестре deck_cards.json.
_SUITS_EN: List[str] = ["Wands", "Cups", "Swords", "Pentacles"]
_RANKS_EN: List[str] = [
    "Ace", "Two", "Three", "Four", "Five", "Six", "Seven",
    "Eight", "Nine", "Ten", "Page", "Knight", "Queen", "King",
]

MAJORS_EN: List[str] = [en for en, _ru in _MAJORS]
MINORS_EN: List[str] = [f"{r} of {s}" for s in _SUITS_EN for r in _RANKS_EN]

#: Все 78 канонических EN-имён, по порядку реестра. Членство в этом наборе —
#: финальный критерий «это карта Уэйта», не «похоже».
WAITE_78_EN: List[str] = MAJORS_EN + MINORS_EN
_WAITE_78_SET = frozenset(WAITE_78_EN)
#: canonical EN → каноничный RU (для шапки/RU-вывода, если понадобится).
WAITE_EN_TO_RU = dict(_MAJORS)


def _norm(s: str) -> str:
    """lower + ё→е + схлоп пробелов; режем всё кроме букв/цифр/пробелов.

    ё→е: парсер и речь дают «влюбленные»/«четверка», словарь — «влюблённые»/
    «четвёрка»; нормализуем обе стороны к е.
    """
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^a-zа-я0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── Лексиконы разбора (нормализованные ключи: lower, ё→е) ─────────────────────

# Старшие: каноничный RU + EN-имя + RU/EN-алиасы + частые мисхёрды.
_MAJOR_LEX: dict = {}


def _reg_major(en: str, *keys: str) -> None:
    for k in keys:
        nk = _norm(k)
        if nk:
            _MAJOR_LEX[nk] = en


_reg_major("The Fool", "Шут", "дурак", "глупец", "fool", "the fool")
_reg_major("The Magician", "Маг", "волшебник", "magician", "the magician")
_reg_major("The High Priestess", "Жрица", "верховная жрица",
           "high priestess", "priestess", "the high priestess")
_reg_major("The Empress", "Императрица", "empress", "the empress")
_reg_major("The Emperor", "Император", "emperor", "the emperor")
_reg_major("The Hierophant", "Иерофант", "жрец", "папа", "hierophant",
           "pope", "the hierophant")
_reg_major("The Lovers", "Влюблённые", "влюбленные", "любовники",
           "lovers", "the lovers")
_reg_major("The Chariot", "Колесница", "chariot", "the chariot")
_reg_major("Strength", "Сила", "strength")
_reg_major("The Hermit", "Отшельник", "hermit", "the hermit")
_reg_major("Wheel of Fortune", "Колесо Фортуны", "колесо фортуны", "фортуна",
           "колесо", "wheel of fortune", "wheel")
_reg_major("Justice", "Справедливость", "правосудие", "justice")
_reg_major("The Hanged Man", "Повешенный", "повешеный", "hanged man",
           "hanged", "the hanged man")
_reg_major("Death", "Смерть", "death")
_reg_major("Temperance", "Умеренность", "temperance")
_reg_major("The Devil", "Дьявол", "devil", "the devil")
_reg_major("The Tower", "Башня", "tower", "the tower")
_reg_major("The Star", "Звезда", "star", "the star")
_reg_major("The Moon", "Луна", "moon", "the moon")
_reg_major("The Sun", "Солнце", "sun", "the sun")
_reg_major("Judgement", "Суд", "суд страшный", "страшный суд",
           "judgement", "judgment")
_reg_major("The World", "Мир", "вселенная", "world", "the world")

# Ранги: RU порядковое/количественное + цифра + EN → canonical EN rank.
_RANK_LEX: dict = {
    "туз": "Ace", "ace": "Ace", "1": "Ace",
    "два": "Two", "двойка": "Two", "2": "Two", "two": "Two",
    "три": "Three", "тройка": "Three", "3": "Three", "three": "Three",
    "четыре": "Four", "четверка": "Four", "4": "Four", "four": "Four",
    "пять": "Five", "пятерка": "Five", "5": "Five", "five": "Five",
    "шесть": "Six", "шестерка": "Six", "6": "Six", "six": "Six",
    "семь": "Seven", "семерка": "Seven", "7": "Seven", "seven": "Seven",
    "восемь": "Eight", "восьмерка": "Eight", "8": "Eight", "eight": "Eight",
    "девять": "Nine", "девятка": "Nine", "9": "Nine", "nine": "Nine",
    "десять": "Ten", "десятка": "Ten", "10": "Ten", "ten": "Ten",
    "паж": "Page", "page": "Page", "11": "Page",
    "рыцарь": "Knight", "knight": "Knight", "12": "Knight",
    "королева": "Queen", "queen": "Queen", "13": "Queen",
    # Фонетические мисхёрды Whisper для «королева» (стабильно искажается).
    "крыльева": "Queen", "кралева": "Queen", "коралева": "Queen",
    "крыльево": "Queen", "королево": "Queen", "каралева": "Queen",
    "король": "King", "king": "King", "14": "King",
}

# Масти: RU (имен./род.) + синонимы + EN + фонетические мисхёрды → canonical EN.
_SUIT_LEX: dict = {
    "жезлы": "Wands", "жезлов": "Wands", "жезл": "Wands",
    "посохи": "Wands", "посохов": "Wands", "wands": "Wands", "wand": "Wands",
    "кубки": "Cups", "кубков": "Cups", "кубок": "Cups",
    "чаши": "Cups", "чаш": "Cups", "cups": "Cups", "cup": "Cups",
    "мечи": "Swords", "мечей": "Swords", "меч": "Swords",
    "swords": "Swords", "sword": "Swords",
    # Фонетический мисхёрд Whisper: «мечей» стабильно слышится как «мячей».
    "мячей": "Swords", "мячи": "Swords", "мяч": "Swords",
    "пентакли": "Pentacles", "пентаклей": "Pentacles", "пентакль": "Pentacles",
    "монеты": "Pentacles", "монет": "Pentacles", "монета": "Pentacles",
    "диски": "Pentacles", "дисков": "Pentacles",
    "pentacles": "Pentacles", "pentacle": "Pentacles",
    "coins": "Pentacles", "coin": "Pentacles",
}

# Слова-МИСХЁРДЫ Whisper (не канон) — маркер «грязного» спана. Спелл портит карту
# в чужую валидную ТОЛЬКО когда исходное слово — мисхёрд (канон-имена 78 защищены
# whitelist'ом спелла). Значит исток спелл-подмены ВСЕГДА содержит мисхёрд-слово,
# а чистая карта в нарративе («король кубков») — нет. Восстанавливаем подменённый
# слот только из «грязных» спанов → одноимённый фантом нарратива не крадёт слот
# (adversarial: surface-похожесть не отличает фантом «{rank} жезлов» от истока).
_MISHEAR_KEYS = frozenset({
    "крыльева", "кралева", "коралева", "крыльево", "королево", "каралева",
    "мячей", "мячи", "мяч",
})


def _span_is_dirty(span: str) -> bool:
    """В спане есть слово-мисхёрд → это (вероятный) исток спелл-подмены, не чистый
    фантом нарратива."""
    return any(w in _MISHEAR_KEYS for w in _norm(span).split(" "))


# ── Детерминированный резолвер ────────────────────────────────────────────────

def _resolve_minor(t0: str, t1: str) -> Optional[str]:
    """Пара токенов «ранг масть» → «{Rank} of {Suit}» ∈ 78, или None.

    Только прямой порядок: карты называют рангом-вперёд и в RU («король кубков»),
    и в EN («King of Cups»). Реверс не нужен и опасен — при скане сырого нарратива
    «…из кубков король сидит…» дал бы ложную King of Cups (раздул бы счёт карт).
    """
    rank = _RANK_LEX.get(t0)
    suit = _SUIT_LEX.get(t1)
    if rank and suit:
        en = f"{rank} of {suit}"
        if en in _WAITE_78_SET:
            return en
    return None


def resolve_waite(phrase: str) -> Optional[str]:
    """Фраза → canonical EN ∈ 78, либо None (не карта / незнакомый мисхёрд).

    Детерминированно, бесплатно, БЕЗ спелла:
      • старший аркан по имени/алиасу/мисхёрду  («жрица» → The High Priestess);
      • младший как РАНГ + МАСТЬ                 («крыльева мячей» → Queen of Swords,
                                                  «7 кубков» → Seven of Cups).
    None — сигнал звать узкий LLM-фоллбэк (classify_waite_card_llm).
    """
    s = _norm(phrase)
    if not s:
        return None
    # 1. Старший аркан — точное имя/алиас (1-3 слова: «the hanged man»).
    if s in _MAJOR_LEX:
        return _MAJOR_LEX[s]
    # 2. Младший — ранг + масть. Убираем связку «of», бьём на токены.
    toks = [t for t in s.replace(" of ", " ").split(" ") if t]
    if len(toks) == 2:
        return _resolve_minor(toks[0], toks[1])
    return None


def _match_at(tokens: List[str], i: int) -> Tuple[Optional[str], int]:
    """Карта, начинающаяся ровно на позиции i → (canonical EN, длина 1-3) | (None, 0).

    Длинная группа пробуется первой: «колесо фортуны»/«the hanged man» (карта)
    раньше «колесо»/«hanged» (не карта в одиночку)."""
    n = len(tokens)
    for length in (3, 2, 1):
        if i + length > n:
            continue
        en = resolve_waite(" ".join(tokens[i:i + length]))
        if en:
            return en, length
    return None, 0


def next_waite(tokens: List[str], cursor: int = 0) -> Tuple[Optional[str], int]:
    """Скан СЫРЫХ токенов с позиции cursor → (canonical EN, индекс-после-карты).

    Идёт вперёд по хвосту, пропуская не-карты (преамбулу, нарратив, вопросы):
    они резолвятся в None. Первая группа 1-3 токенов, что даёт карту ∈ 78, —
    результат. Не нашли до конца → (None, cursor) (курсор не двигаем).
    """
    n = len(tokens)
    i = max(0, cursor)
    while i < n:
        en, length = _match_at(tokens, i)
        if en:
            return en, i + length
        i += 1
    return None, cursor


# ── Узкий LLM-фоллбэк (Haiku) ─────────────────────────────────────────────────

_LLM_LIST = ", ".join(WAITE_78_EN)
_LLM_SYSTEM = (
    "Ты сопоставляешь короткую фразу (возможно искажённую распознаванием речи) с "
    "ОДНОЙ картой Таро Райдера-Уэйта.\n"
    "Верни ТОЛЬКО каноничное АНГЛИЙСКОЕ имя карты ИЗ СПИСКА НИЖЕ, дословно, без "
    "кавычек и пояснений. Если фраза НЕ похожа ни на одну карту — верни ровно "
    "null.\n"
    "СПИСОК (78):\n" + _LLM_LIST
)


async def classify_waite_card_llm(phrase: str) -> Optional[str]:
    """Незнакомый мисхёрд, который детерминированный код не разобрал, → Haiku.

    Узкая классификация «фраза → одна из 78 | null» (Haiku по CLAUDE.md —
    deep reasoning не нужен; cost-страж test_models_audit держит дешёвую модель).
    Результат ЖЁСТКО проверяется на членство в 78 — «похоже» не принимается.
    Любая ошибка вызова/непопадание → None (graceful, как везде в проекте).
    """
    if not phrase or not phrase.strip():
        return None
    try:
        from core.claude_client import ask_claude
        out = await ask_claude(
            phrase.strip(),
            system=_LLM_SYSTEM,
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            temperature=0,
        )
    except Exception as e:  # noqa: BLE001 — graceful, не роняем расклад
        logger.warning("waite LLM fallback error: %s", e)
        return None
    cand = (out or "").strip().strip('"').strip("«»").strip()
    if not cand or cand.lower() == "null":
        return None
    # Жёсткое членство: точное совпадение, иначе пробуем как фразу через резолвер
    # (вдруг модель вернула RU/искажение) — но без членства в 78 не принимаем.
    if cand in _WAITE_78_SET:
        return cand
    return resolve_waite(cand)


async def resolve_or_llm(phrase: str) -> Optional[str]:
    """Полный гибрид: детерминированно, иначе Haiku-фоллбэк. EN ∈ 78 | None."""
    en = resolve_waite(phrase)
    if en:
        return en
    return await classify_waite_card_llm(phrase)


# ── Оркестрация: нормализация карт парс-результата (Уэйт) ─────────────────────

def _iter_blocks(data: dict) -> List[dict]:
    """Блоки расклада в порядке речи: сам data (single) + триплеты (multi).

    Зеркалит покрытие grounding (_ground_block(data) + triplets). В multi data
    обычно без cards (карты внутри триплетов) → даёт 0 слотов.
    """
    blocks = [data]
    for it in (data.get("triplets") or data.get("items") or []):
        if isinstance(it, dict):
            blocks.append(it)
    return blocks


def _block_slots(block: dict) -> List[Tuple[str, int]]:
    """Слоты блока в порядке речи: карты, затем дно (если есть). ('cards', i) /
    ('bottom', -1)."""
    slots: List[Tuple[str, int]] = []
    cards = block.get("cards")
    if isinstance(cards, list):
        slots.extend(("cards", i) for i in range(len(cards)))
    bottom = block.get("bottom_card")
    if isinstance(bottom, str) and bottom.strip():
        slots.append(("bottom", -1))
    return slots


def _get_slot(block: dict, key: str, idx: int) -> str:
    if key == "cards":
        return str((block.get("cards") or [None])[idx] or "")
    return str(block.get("bottom_card") or "")


def _set_slot(block: dict, key: str, idx: int, value: str) -> None:
    if key == "cards":
        block["cards"][idx] = value
    else:
        block["bottom_card"] = value


def _scan_raw_pairs(raw_ref: str) -> List[Tuple[str, str]]:
    """Карты СЫРОГО транскрипта по порядку как (canonical EN, дословный спан).

    Спан (исходные слова, что дали карту) нужен для сопоставления неподтверждённого
    слота с его настоящим истоком по тексту (см. _assign_waite_slots)."""
    tokens = (raw_ref or "").split()
    out: List[Tuple[str, str]] = []
    i, n = 0, len(tokens)
    while i < n:
        en, length = _match_at(tokens, i)
        if en:
            out.append((en, " ".join(tokens[i:i + length])))
            i += length
        else:
            i += 1
    return out


def _assign_waite_slots(
    enps: List[Optional[str]], raw_pairs: List[Tuple[str, str]],
) -> List[Optional[str]]:
    """Слоты парсера → карта сырья (EN) для каждого, или None (нет назначения).

    Якорь + позиционное восстановление из «грязных» спанов (ЧИСТО детерминированно,
    без SequenceMatcher):

      1. ЯКОРЯ — слоты, чей enP дословно встречается в сыром ПО ПОРЯДКУ (жадно
         вперёд). ИНВАРИАНТ: ВЕРНАЯ карта совпадает с сырьём → всегда якорь. В
         дырки попадают ТОЛЬКО неподтверждённые слоты (спелл-подмена / мисхёрд).
      2. ДЫРКА — восстановление ТОЛЬКО из «грязных» (содержащих мисхёрд-слово)
         спанов, и ТОЛЬКО для ELIGIBLE слотов = неподтверждённых С ВАЛИДНЫМ enP.
         Почему «грязь»: спелл-подмена бьёт лишь по мисхёрдам (канон-78 защищён
         whitelist'ом спелла) → исток подмены ВСЕГДА грязный, чистая карта
         нарратива («король кубков») — нет, даже если шарит масть/ранг с фразой.
         Почему eligible=enP-set: грязный спан — это подмена, у которой парсер
         прочитал искажённую форму и выдал ВАЛИДНУЮ-но-чужую карту (enP set).
         Novel-слот (enP=None) = парсер не разобрал = в сыром ГАРБЛ, а не грязный
         резолвящийся спан → грязный спан ему НЕ принадлежит (иначе novel-слот с
         уронённой картой крал бы origin соседа — adversarial «двойной сбой»).
         Раскладка 1:1 ТОЛЬКО при совпадении счёта (грязных == eligible): иначе
         origin уронен / лишний мисхёрд → НЕ гадаем, eligible держат enP.
         Грязные и eligible идут в одном порядке (диктовка линейна) → zip.

    None для слота = нет своего грязного истока → выше берём enP или Haiku.
    """
    n_slots, n_raw = len(enps), len(raw_pairs)
    out: List[Optional[str]] = [None] * n_slots
    raw_ens = [en for en, _ in raw_pairs]

    # 1. якоря: жадное прямое сопоставление enP → позиция в сыром
    anchors: List[Tuple[int, int]] = []
    ri = 0
    for sp, enP in enumerate(enps):
        if enP is None:
            continue
        j = next((t for t in range(ri, n_raw) if raw_ens[t] == enP), None)
        if j is not None:
            anchors.append((sp, j))
            ri = j + 1

    # 2. дырки: грязные (мисхёрд) спаны → ELIGIBLE (enP-set) слотам, 1:1 при
    #    совпадении счёта. Novel-слоты (enP=None) грязный спан НЕ берут (их карта —
    #    гарбл) → идут в Haiku; при несовпадении счёта eligible держат enP.
    prev_sp, prev_rp = -1, -1
    for a_sp, a_rp in anchors + [(n_slots, n_raw)]:
        eligible = [
            s for s in range(prev_sp + 1, a_sp) if enps[s] is not None
        ]
        dirty_cands = [
            c for c in range(prev_rp + 1, a_rp) if _span_is_dirty(raw_pairs[c][1])
        ]
        if len(eligible) == len(dirty_cands):
            for s, c in zip(eligible, dirty_cands):
                out[s] = raw_pairs[c][0]             # подменённая карта из истока
        # счёт не сошёлся (origin уронен / лишний мисхёрд) → НЕ гадаем: enP/Haiku
        if a_sp < n_slots:
            out[a_sp] = raw_pairs[a_rp][0]          # сам якорь (== enP)
        prev_sp, prev_rp = a_sp, a_rp

    return out


async def normalize_waite_cards_in_data(data: dict, raw_ref: str) -> None:
    """In-place: карты Уэйт-расклада → canonical EN ∈ 78. Заменяет grounding+canon.

    Якорь — СЛОТЫ ПАРСЕРА (он отделил карты от нарратива/вопросов). Сырой
    транскрипт ``raw_ref`` (голос = Whisper ДО спелла; текст = сам текст) —
    источник истины: слоты выравниваются к реальным картам сырья
    (:func:`_assign_waite_slots`). Чинит спелл-подмену («король жезлов» из
    «крыльева мячей» → Queen of Swords), игнорирует фантомы нарратива («десять
    монет», «три чаши», одиночные «сила/мир/луна»), восстанавливает даже карту,
    которую ПАРСЕР не разобрал, если в сыром она чистая — без позиционного сдвига,
    потери дна и затирания верной карты парсера фантомом (всё это ломало
    эвристики, см. adversarial-review).

    На каждый слот:
      • назначена карта сырья → берём её (матч / починка подмены / восстановление);
      • сырьё не назначено, но enP валиден → enP (карта не прозвучала / фантом в дырке);
      • сырьё не назначено и enP=None → узкий Haiku-фоллбэк, иначе дословно (Кай поправит).
    Дно — только там, где парсер выделил (следит за маркером «дно»).
    """
    if not isinstance(data, dict):
        return
    slots: List[Tuple[dict, str, int]] = []
    for b in _iter_blocks(data):
        for key, idx in _block_slots(b):
            slots.append((b, key, idx))
    if not slots:
        return

    raw_pairs = _scan_raw_pairs(raw_ref)      # (EN, спан) карт сырья по порядку
    enps = [resolve_waite(_get_slot(b, k, i)) for (b, k, i) in slots]
    assigned = _assign_waite_slots(enps, raw_pairs)

    for (b, key, idx), enP, raw_card in zip(slots, enps, assigned):
        if raw_card is not None:
            result = raw_card                 # матч / починка / восстановление
        elif enP is not None:
            result = enP                      # карта не прозвучала / фантом → парсер
        else:
            phrase = _get_slot(b, key, idx)
            result = await classify_waite_card_llm(phrase) or phrase
        _set_slot(b, key, idx, result)
