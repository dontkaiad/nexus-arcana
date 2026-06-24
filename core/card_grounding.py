"""core/card_grounding.py — граундинг карт парсера в транскрипт.

Recon-диагноз (#166-след): промпт-правила Haiku игнорит, проверка по 78 картам
не ловит выдумку (валидная-но-ЧУЖАЯ карта «Король Жезлов» из «крыльева мячей» —
она ЕСТЬ среди 78). Единственный источник истины — транскрипт.

Идея: после парсинга сверяем КАЖДУЮ карту со словами транскрипта по фонетико-
лексической близости (SequenceMatcher на нормализованных словах, ё→е). Если
слова карты не грундятся (далеки от всего в транскрипте выше порога) — карта
выдумана; заменяем её на ДОСЛОВНЫЙ фрагмент транскрипта (его потом нормализатор
смапит алиасами: «крыльева мячей» → «Королева Мечей»).

Два нюанса делают замену надёжной:
- БИЕКЦИЯ слов карты к словам окна — чтобы и ранг, и масть имели СВОЮ опору, а не
  липли к одному похожему слову.
- ЯКОРЬ замены на РАНГЕ + КУРСОР по порядку карт — выдуманная масть («жезлов») не
  матчит ничего, а слова-вопроса/одинаковые ранги («король кубков» перед «Король
  Жезлов») сбивали бы выбор фрагмента. Курсор не даёт карте N схватить регион
  карты N-1.

Порог 0.75 подобран на примерах (см. tests/test_card_grounding.py):
  REJECT  «Король Жезлов» vs «крыльева мячей» ~0.40
  REJECT  «Королева Жезлов» (частичная подмена) ~0.59
  ACCEPT  лёгкое искажение «мечей»↔«мячей» ~0.90 ; «пентакли»↔«пентаклей» ~0.91
Зазор [0.59 … 0.90] — порог 0.75 делит чисто.
"""
from __future__ import annotations

import re
import logging
from difflib import SequenceMatcher
from itertools import permutations
from typing import List, Tuple

logger = logging.getLogger("arcana.grounding")

GROUND_THRESHOLD = 0.75
# Near-miss: лучшее окно карты в СЫРОМ транскрипте 0.60..0.75 — обычно ТА ЖЕ
# карта с опечаткой/искажением ранга мисхёрда (Whisper «тус пентакли», спелл
# починил → парсер «Туз Пентаклей»; против сырого верная карта скорит ~0.71-
# 0.745). Грань 0.60 отделяет опечатку (~0.71+) от чистого шума (~0.35, где на
# позиции реально ДРУГАЯ карта). В этой полосе не воруем соседний фрагмент, если
# на позиции не стоит другая резолвящаяся карта (см. _names_other_card).
GROUND_NEAR_MISS = 0.60

_STRIP = re.compile(r"[^а-яa-z0-9]")
_SPLIT = re.compile(r"\s+")
_MAX_PERM_WORDS = 4  # k! перебор соответствий — только для коротких имён карт


def _norm(word: str) -> str:
    return _STRIP.sub("", (word or "").lower().replace("ё", "е"))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _card_words(card: str) -> List[str]:
    return [n for n in (_norm(w) for w in _SPLIT.split(card or "")) if n]


def _tokens(transcript: str) -> Tuple[List[str], List[str]]:
    """(raw_words, norm_words) — параллельные; пустые-после-нормализации убраны.
    raw нужны для дословной замены (с ё, чтобы алиас нормализатора сработал)."""
    raw = [w for w in _SPLIT.split(transcript or "") if w.strip()]
    pairs = [(r, _norm(r)) for r in raw]
    pairs = [(r, n) for r, n in pairs if n]
    return [r for r, _ in pairs], [n for _, n in pairs]


def _bijective(card_words: List[str], window: List[str]) -> float:
    """Лучшее 1-к-1 соответствие слов карты словам окна (среднее по парам)."""
    k = len(card_words)
    if k == 0 or not window:
        return 0.0
    if k > _MAX_PERM_WORDS:
        return sum(max((_ratio(c, t) for t in window), default=0.0)
                   for c in card_words) / k
    best = 0.0
    for perm in permutations(window, min(k, len(window))):
        s = sum(_ratio(c, t) for c, t in zip(card_words, perm)) / k
        if s > best:
            best = s
    return best


def _best_window(
    card_words: List[str], norm_words: List[str], start: int,
    lookahead: int = -1,
) -> Tuple[float, int]:
    """(score, idx) лучшего скользящего окна длины k во ВСЁМ хвосте start..конец.

    Реальная карта названа ГДЕ-ТО в транскрипте (после преамбулы «Расклад на X.
    Клиента Я. Колода…», вопросов, нарратива) → ищем её по всему хвосту, иначе
    карта тонет (score ~0.2) и рабочий расклад портится. score = близость карты
    к её ЛУЧШЕМУ окну, НЕ ко всему транскрипту. lookahead (по умолчанию -1 = без
    лимита) оставлен параметром для возможных точечных вызовов; курсор сам не даёт
    карте N схватить регион карты N-1."""
    k = len(card_words)
    if k == 0 or start >= len(norm_words):
        return 0.0, start
    end = len(norm_words) - k + 1
    if lookahead >= 0:
        end = min(end, start + lookahead + 1)
    if end <= start:  # хвост короче карты — одно окно от start
        return _bijective(card_words, norm_words[start:]), start
    best_s, best_i = -1.0, start
    for i in range(start, end):
        s = _bijective(card_words, norm_words[i:i + k])
        if s > best_s:
            best_s, best_i = s, i
    return best_s, best_i


def _rank_anchor(rank: str, norm_words: List[str], start: int) -> int:
    """Позиция (>= start), где РАНГ карты лучше всего совпадает: выдуманная масть
    не помогает, ранг («король»/«туз») — самое различимое слово."""
    rng = range(start, len(norm_words))
    if not rng:
        return start
    return max(rng, key=lambda i: _ratio(rank, norm_words[i]))


def _recover_span(
    card_words: List[str], raw: List[str], norm: List[str], cursor: int, resolver
) -> Tuple[str, int, int]:
    """Дословный фрагмент транскрипта под выдуманную карту, начиная с cursor.

    (1) Если есть resolver (нормализатор колоды) — берём ПЕРВЫЙ фрагмент, который
    РЕАЛЬНО резолвится в карту: «крыльева мячей»→Королева Мечей резолвится, шум
    «на ценностях» — нет. Это надёжнее фонетической близости (короткие слова-
    паразиты «на»/«не» липнут к коротким рангам «паж»/«туз»).
    (2) Без resolver — fallback: окно с якорем на ранге."""
    if resolver:
        # Длину карты пробуем ПЕРВОЙ: для 1-словной карты («влюблённые») сначала
        # 1-словные спаны, иначе схватили бы соседний 2-словный («крыльева мячей»).
        kc = len(card_words)
        for k in [kc] + [x for x in (2, 1) if x != kc]:
            if k < 1:
                continue
            for i in range(cursor, len(raw) - k + 1):
                span = " ".join(raw[i:i + k])
                if resolver(span):
                    return span, i, k
    k = len(card_words)
    anchor = _rank_anchor(card_words[0], norm, cursor)
    start = min(anchor, max(cursor, len(raw) - k)) if len(raw) >= k else cursor
    return (" ".join(raw[start:start + k]) if raw else ""), start, k


def _names_other_card(card: str, span_words: List[str], resolver) -> bool:
    """На позиции карты в сыром транскрипте стоит ДРУГАЯ реальная карта (подмена),
    а не та же карта с опечаткой мисхёрда и не шум?

    resolver(span) → каноничное имя карты колоды или None. «крыльево мячей»
    резолвится в Королева Мечей; «тус пентакли» (искажение ранга) и шум — нет.
    Окно резолвится в карту И (карта парсера НЕ резолвится — мусор-мисхёрд
    «каралева меча» — ЛИБО резолвится в ДРУГУЮ карту) → подмена, восстанавливаем.
    Окно не резолвится (шум/опечатка) или та же карта → держим карту парсера."""
    if not resolver:
        return False
    sid = resolver(" ".join(span_words))
    if not sid:
        return False
    cid = resolver(card)
    # cid is None: карта парсера — нераспознаваемый мусор, а на позиции стоит
    # реальная карта → это лучше, чем мусор → восстанавливаем (не near-miss keep).
    return (cid is None) or (sid != cid)


def ground_cards(
    cards, transcript: str, threshold: float = GROUND_THRESHOLD, resolver=None
) -> List[str]:
    """Сверяет список карт парсера (по ПОРЯДКУ) с транскриптом. Негрундящиеся →
    дословный фрагмент транскрипта (resolver находит тот, что резолвится в карту).
    Курсор идёт по транскрипту, чтобы карта N не схватила регион карты N-1."""
    cards = list(cards or [])
    if not transcript:
        return cards
    raw, norm = _tokens(transcript)
    if not norm:
        return cards

    out: List[str] = []
    cursor = 0
    for card in cards:
        cw = _card_words(card) if (card and card.strip()) else []
        if not cw:
            out.append(card)
            continue
        k = len(cw)
        score, idx = _best_window(cw, norm, cursor)
        if score >= threshold:
            out.append(card)
            cursor = min(idx + k, len(norm))
            logger.info("grounding: %r score=%.2f >= %.2f → keep", card, score, threshold)
        elif (
            k >= 2
            and score >= GROUND_NEAR_MISS
            and not _names_other_card(card, raw[idx:idx + k], resolver)
        ):
            # Near-miss к ТОЙ ЖЕ карте: лучшее окно сырого транскрипта — та же карта
            # с опечаткой/искажением ранга (Whisper «тус пентакли» → спелл починил →
            # парсер «Туз Пентаклей»; против сырого ~0.745). На позиции НЕ стоит
            # другая реальная карта → держим карту парсера, НЕ воруем соседний
            # фрагмент (иначе уничтожали бы верную карту — регрессия граундинга).
            # k>=2 ОБЯЗАТЕЛЕН: 1-словный мажор латчится за случайное общее слово
            # («Луна»←«на», «Колесница»←«клиента», score 0.6-0.67) и держал бы
            # НЕназванную карту. ≥2 слов = ранг+масть дают корроборацию.
            out.append(card)
            cursor = min(idx + k, len(norm))
            logger.info("grounding: %r score=%.2f near-miss → keep", card, score)
        else:
            span, s_idx, s_k = _recover_span(cw, raw, norm, cursor, resolver)
            out.append(span or card)
            cursor = min(s_idx + s_k, len(norm))
            logger.info(
                "grounding: %r score=%.2f < %.2f → replace with %r",
                card, score, threshold, span or card,
            )
    return out


def ground_card(
    card: str, transcript: str, threshold: float = GROUND_THRESHOLD, resolver=None
) -> str:
    """Одна карта (без контекста порядка). Для bottom_card и юнит-тестов."""
    if not card or not card.strip():
        return card
    return ground_cards([card], transcript, threshold, resolver)[0]


def _ground_block(block: dict, transcript: str, threshold: float, resolver) -> None:
    """In-place: грундит cards + bottom_card блока ОДНОЙ упорядоченной
    последовательностью (дно идёт после карт → курсор течёт сквозь оба,
    bottom не схватит регион ранней одноимённой карты)."""
    cards = block.get("cards") if isinstance(block.get("cards"), list) else None
    bottom = block.get("bottom_card")
    has_bottom = isinstance(bottom, str) and bottom.strip()
    if cards is None and not has_bottom:
        return
    seq = (cards or []) + ([bottom] if has_bottom else [])
    grounded = ground_cards(seq, transcript, threshold, resolver)
    if cards is not None:
        block["cards"] = grounded[:len(cards)]
    if has_bottom:
        block["bottom_card"] = grounded[-1]


def ground_cards_in_data(
    data: dict, transcript: str, threshold: float = GROUND_THRESHOLD, resolver=None
) -> None:
    """In-place граундинг карт парс-результата: single (cards + bottom_card) +
    multi (triplets[].cards + bottom_card). Один вызов — оба флоу. resolver(span)
    → каноничное имя карты колоды или None (для надёжной замены и распознавания
    подмены vs опечатки в near-miss; см. _names_other_card)."""
    if not isinstance(data, dict) or not transcript:
        logger.info("grounding: SKIP (no data/transcript; transcript_len=%d)",
                    len(transcript or ""))
        return
    n_tri = len(data.get("triplets") or data.get("items") or [])
    logger.info("grounding: START transcript_len=%d single_cards=%d triplets=%d resolver=%s",
                len(transcript), len(data.get("cards") or []), n_tri, resolver is not None)
    _ground_block(data, transcript, threshold, resolver)
    for item in (data.get("triplets") or data.get("items") or []):
        if isinstance(item, dict):
            _ground_block(item, transcript, threshold, resolver)
