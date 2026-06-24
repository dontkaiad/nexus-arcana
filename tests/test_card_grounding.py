"""tests/test_card_grounding.py — граундинг карт парсера в транскрипт.

Парсер подменяет искажённое слово валидной-но-ЧУЖОЙ картой («крыльева мячей» →
«Король Жезлов»). Промпт-правила Haiku игнорит, проверка по 78 картам не ловит
(чужая карта тоже валидна). Сверяем карту с транскриптом: негрундящуюся заменяем
на дословный фрагмент → нормализатор смапит алиасами в правильную карту.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.card_grounding import (
    GROUND_THRESHOLD,
    ground_card,
    ground_cards,
    ground_cards_in_data,
    _best_window,
    _card_words,
    _tokens,
)

REPO = Path(__file__).resolve().parent.parent


# ───────────────────────── решение keep / replace ──────────────────────────

# (карта парсера, транскрипт, грундится ли)
GROUNDING = [
    # грубая подмена: «Король Жезлов» из «крыльева мячей» → НЕ грундится
    ("Король Жезлов", "что он чувствует ко мне крыльева мячей шут жрица", False),
    # частичная подмена масти → НЕ грундится
    ("Королева Жезлов", "королева мячей шут маг", False),
    # точное совпадение → грундится
    ("Королева Мечей", "королева мечей шут маг", True),
    # склонение «пентакли»→«пентаклей» → грундится
    ("Туз Пентаклей", "туз пентакли дно король", True),
    # лёгкий мисхёрд масти «мечей»↔«мячей» → грундится
    ("Девятка Мечей", "девятка мячей шут жрица", True),
    # Старший аркан назван точно → грундится
    ("Шут", "крыльева мячей шут жрица", True),
]


@pytest.mark.parametrize("card,transcript,grounded", GROUNDING)
def test_ground_decision(card, transcript, grounded):
    out = ground_card(card, transcript)
    if grounded:
        assert out == card, f"{card!r} должна остаться (грундится в транскрипт)"
    else:
        assert out != card, f"{card!r} выдумана — должна замениться на фрагмент транскрипта"


def test_threshold_separates_cases():
    """Порог 0.75 делит REJECT (≤0.59) и ACCEPT (≥0.90) — показываем значения."""
    scores = {}
    for card, transcript, grounded in GROUNDING:
        cw = _card_words(card)
        _, norm = _tokens(transcript)
        scores[(card, grounded)] = _best_window(cw, norm, 0)[0]
    rejects = [s for (c, g), s in scores.items() if not g]
    accepts = [s for (c, g), s in scores.items() if g]
    assert max(rejects) < GROUND_THRESHOLD <= min(accepts), (
        f"порог {GROUND_THRESHOLD} не делит: reject_max={max(rejects):.3f}, "
        f"accept_min={min(accepts):.3f}"
    )


# ───────────────────── цепочка целиком (ground→norm→lookup) ─────────────────

def test_full_chain_hallucination_to_correct_card():
    """«Король Жезлов» из «крыльева мячей»: ground → дословно → нормализатор →
    «Королева Мечей» (находится в справочнике waite)."""
    from miniapp.backend.tarot import normalize_card_input
    from arcana.tarot_loader import _lookup_card, _load_deck, get_deck_file

    grounded = ground_card("Король Жезлов", "крыльева мячей шут жрица")
    assert grounded == "крыльева мячей"  # дословный фрагмент
    assert normalize_card_input(grounded) == "королева мечей"  # алиасы
    dd = _load_deck(get_deck_file("Уэйт"))
    assert _lookup_card(dd, "Уэйт", grounded), "грундированная карта не нашлась в waite"


def _waite_resolver():
    from miniapp.backend.tarot import resolve_deck_id, find_card
    did = resolve_deck_id("Уэйт")
    # каноничное имя карты или None (near-miss отличает подмену от опечатки)
    return lambda s: (find_card(did, s) or {}).get("ru") or None


def test_recovery_skips_noise_via_resolver_real_case():
    """Реальный кейс (скрин): парсер выдал «паж жезлов» по ОПИСАНИЮ Кай (резкая/
    порывистая = ключи Пажа Жезлов), хотя названа Королева Мечей → Whisper
    «крыльева мячей». Длинный режим-A транскрипт: без resolver замена цеплялась
    за шум «на ценностях»; с resolver — берём фрагмент, что резолвится в карту."""
    from miniapp.backend.tarot import normalize_card_input
    transcript = (
        "что вадим чувствует прямо сейчас туз пентаклей это реальный материальный "
        "шанс новая работа деньги влюбленные внутри выбор завязанный на ценностях "
        "крыльева мячей женщина которая ранит резкая порывистая дно семь кубков иллюзия"
    )
    data = {"cards": ["туз пентаклей", "влюбленные", "паж жезлов"],
            "bottom_card": "семь кубков"}
    ground_cards_in_data(data, transcript, resolver=_waite_resolver())
    assert data["cards"][0] == "туз пентаклей"
    assert data["cards"][1] == "влюбленные"
    assert data["cards"][2] == "крыльева мячей", "не восстановил названную карту из транскрипта"
    assert normalize_card_input(data["cards"][2]) == "королева мечей"  # → Королева Мечей


def test_resolver_recovery_beats_similarity_noise():
    """С resolver выдуманная 3-я карта восстанавливается из «крыльева мячей»,
    а не из шума «на ценностях» (курсор идёт по порядку карт)."""
    from core.card_grounding import ground_cards
    transcript = "туз пентаклей влюбленные на ценностях крыльева мячей шут"
    cards = ["туз пентаклей", "влюбленные", "паж жезлов"]
    grounded = ground_cards(cards, transcript, resolver=_waite_resolver())
    assert grounded[2] == "крыльева мячей", f"resolver не нашёл карту, дал {grounded[2]!r}"


# ───────────────────── мутация data (single + multi + дно) ──────────────────

def test_ground_cards_in_data_single_and_bottom():
    data = {
        "cards": ["король кубков", "Король Жезлов", "шут"],
        "bottom_card": "Король Жезлов",
    }
    ground_cards_in_data(data, "король кубков крыльева мячей шут дно крыльева мячей")
    assert data["cards"][0] == "король кубков"   # грундится — цела
    assert data["cards"][1] == "крыльева мячей"  # выдумка → фрагмент
    assert data["cards"][2] == "шут"
    assert data["bottom_card"] == "крыльева мячей"


def test_ground_cards_in_data_multi():
    """Multi-флоу: карты КАЖДОГО триплета грундятся; легитимные целы, выдумки заменены."""
    transcript = "крыльева мячей шут жрица туз кубков маг луна"
    data = {"triplets": [
        {"cards": ["Король Жезлов", "шут", "жрица"]},   # 1-я выдумана
        {"cards": ["туз кубков", "маг", "луна"]},       # все легитимны
    ]}
    ground_cards_in_data(data, transcript)
    assert data["triplets"][0]["cards"] == ["крыльева мячей", "шут", "жрица"]
    assert data["triplets"][1]["cards"] == ["туз кубков", "маг", "луна"]


def test_legit_card_present_in_transcript_kept():
    """Реальная карта, реально названная → НЕ трогаем, даже если соседняя выдумана."""
    out = ground_card("Шут", "король жезлов шут жрица")
    assert out == "Шут"


def test_no_transcript_is_noop():
    data = {"cards": ["Король Жезлов"]}
    ground_cards_in_data(data, "")
    assert data["cards"] == ["Король Жезлов"]  # без транскрипта не трогаем


# ───────────────────── промпт: убран негативный few-shot ────────────────────

def test_negative_fewshot_removed_from_parse_prompt():
    from arcana.handlers.sessions import PARSE_SESSION_SYSTEM as p
    # негативный пример с конкретной картой убран (priming, recon)
    assert "НЕ «Король Жезлов»" not in p
    # позитивная формулировка дословного fallback на месте
    assert "перенеси ДОСЛОВНЫЙ фрагмент" in p


def test_grounding_wired_after_parse():
    """Source-guard: handle_add_session зовёт граундинг сразу после парсинга,
    ДО split single/multi (покрывает оба флоу)."""
    src = (REPO / "arcana" / "handlers" / "sessions.py").read_text(encoding="utf-8")
    i_parse = src.index("system=PARSE_SESSION_SYSTEM")
    i_ground = src.index("ground_cards_in_data(")
    i_multi = src.index("_handle_multi_session(")
    assert i_parse < i_ground < i_multi, "граундинг должен быть после парса и до multi-split"
    # резолвер колоды прокинут (возвращает имя карты/None для near-miss)
    assert "resolver=lambda s: (find_card(_gr_deck, s)" in src


def test_real_cards_after_preamble_kept_and_hallucination_recovers():
    """Режим A: преамбула («Расклад на X. Клиент Я. Колода…») + вопрос ПЕРЕД
    картами. Граундинг ищет карту по ВСЕМУ хвосту, поэтому реальная карта после
    преамбулы (туз пентакли на idx≈12) остаётся, а выдумка «король жезлов» из
    «крыльево мячей» восстанавливается. Нарратив ОПИСЫВАЕТ карту словами, не
    называя чужую — ложно не грундится."""
    transcript = (
        "расклад на отношения клиент я колода уэйт что человек чувствует прямо сейчас "
        "туз пентакли влюбленные крыльево мячей дно семь кубков "
        "эта женщина огненная резкая властная она его ранила"
    )
    data = {"cards": ["туз пентакли", "влюбленные", "король жезлов"],
            "bottom_card": "семь кубков"}
    ground_cards_in_data(data, transcript, resolver=_waite_resolver())
    assert data["cards"][0] == "туз пентакли", "реальная карта после преамбулы испорчена"
    assert data["cards"][1] == "влюбленные"
    assert data["cards"][2] == "крыльево мячей", "выдумка не восстановилась в названную карту"
    from miniapp.backend.tarot import normalize_card_input
    assert normalize_card_input(data["cards"][2]) == "королева мечей"


def test_long_transcript_real_cards_all_kept():
    """Крит-баг #172: на длинном голосовом транскрипте (преамбула + 9 триплетов +
    нарратив) lookahead топил ПРАВИЛЬНЫЕ карты (score ~0.2) и заменял мусором —
    27/27 испорчено. Без lookahead реальная карта находит окно по всему хвосту →
    KEEP. Все карты названы верно → ни одной замены."""
    transcript = (
        "расклад на отношения клиент я колода уэйт что человек чувствует прямо сейчас "
        "туз пентакли влюбленные крыльево мячей дно семь кубков "
        "по тузу пентакли реальный материальный шанс влюбленные выбор "
        "кто эта крыльева мячей пятерка пентакли восьмерка кубков восемь пентакли "
        "какой шанс шесть кубков императрица рыцарь кубков "
        "планы на меня колесница рыцарь мячей паж мячей "
        "почему ничего не делает мир паж жезлов сила "
        "внутренние блоки девять мечей тройка жезлов жрица "
        "чего я не знаю пять жезлов десять жезлов пять кубков "
        "проявится ли десять мечей паж кубков паж пентакли "
        "планы на отношения два жезлов король жезлов королева жезлов"
    )
    triplets = [
        ["туз пентакли", "влюбленные", "крыльево мячей"],
        ["пятерка пентакли", "восьмерка кубков", "восемь пентакли"],
        ["шесть кубков", "императрица", "рыцарь кубков"],
        ["колесница", "рыцарь мячей", "паж мячей"],
        ["мир", "паж жезлов", "сила"],
        ["девять мечей", "тройка жезлов", "жрица"],
        ["пять жезлов", "десять жезлов", "пять кубков"],
        ["десять мечей", "паж кубков", "паж пентакли"],
        ["два жезлов", "король жезлов", "королева жезлов"],
    ]
    data = {"triplets": [{"cards": list(t)} for t in triplets]}
    ground_cards_in_data(data, transcript, resolver=_waite_resolver())
    for orig, block in zip(triplets, data["triplets"]):
        assert block["cards"] == orig, (
            f"граундинг испортил правильные карты: {block['cards']} != {orig}"
        )


@pytest.mark.parametrize("said,parsed", [
    ("туз мячей", "туз жезлов"),
    ("девятка мячей", "девятка жезлов"),
    ("паж мячей", "паж жезлов"),
    ("рыцарь мячей", "рыцарь жезлов"),
])
def test_swords_systematic_not_grounded_to_wands(said, parsed):
    """Системно: парсер выдаёт X ЖЕЗЛОВ из X МЯЧЕЙ (мечей) — НЕ должно
    грундиться; восстанавливается в Мечи (вся масть, не только королева)."""
    from miniapp.backend.tarot import normalize_card_input
    transcript = f"что чувствует {said} шут маг это про конфликт мысли борьбу"
    out = ground_card(parsed, transcript, resolver=_waite_resolver())
    assert out != parsed, f"{parsed!r} ложно сгрундилось как Жезлы"
    assert "мечей" in normalize_card_input(out), f"не восстановилось в Мечи: {out!r}"


# ───────────── граундинг против СЫРОГО транскрипта, не спелл-вывода ──────────

def test_poison_spell_text_vs_raw_transcript():
    """Корень бага: спелл (normalize_text) переписал «крыльево мячей» → «король
    жезлов» ДО парсера. Граундинг против СПЕЛЛ-текста сверяет яд с ядом (1.00
    keep) — карта НЕ чинится. Против СЫРОГО транскрипта «король жезлов» не
    грундится → recover → дословный фрагмент → алиас → Королева Мечей."""
    from miniapp.backend.tarot import normalize_card_input
    cards = ["туз пентаклей", "влюбленные", "король жезлов"]  # 3-я отравлена спеллом
    spell_text = ("что человек чувствует прямо сейчас туз пентаклей влюбленные "
                  "король жезлов дно семь кубков")
    raw_text = ("что человек чувствует прямо сейчас туз пентакли влюбленные "
                "крыльево мячей дно семь кубков")
    d_spell = {"cards": list(cards), "bottom_card": "семь кубков"}
    d_raw = {"cards": list(cards), "bottom_card": "семь кубков"}
    ground_cards_in_data(d_spell, spell_text, resolver=_waite_resolver())
    ground_cards_in_data(d_raw, raw_text, resolver=_waite_resolver())
    # против спелл-текста — баг воспроизводится (яд уцелел)
    assert d_spell["cards"][2] == "король жезлов"
    # против сырого — восстановлено реально сказанное
    assert d_raw["cards"][2] == "крыльево мячей"
    assert normalize_card_input(d_raw["cards"][2]) == "королева мечей"


def test_spell_repaired_rank_mishear_not_destroyed_by_raw():
    """Регрессия (ревью raw-ref): Whisper исказил РАНГ — «тус пентакли» (т/с swap).
    Спелл ПРАВИЛЬНО починил → парсер выдал «туз пентаклей». Граундинг против
    сырого «тус пентакли» скорит 0.745 < 0.75, НО «тус пентакли» не резолвится
    (не алиас) → near-miss keep: держим карту парсера, НЕ воруем «семь кубков».
    Без фикса карта[0] превращалась в «семь кубков» + дубль на дне."""
    from miniapp.backend.tarot import normalize_card_input
    data = {"cards": ["туз пентаклей", "влюбленные", "шут"], "bottom_card": "семь кубков"}
    raw = "что чувствует тус пентакли влюбленные шут дно семь кубков"
    ground_cards_in_data(data, raw, resolver=_waite_resolver())
    assert data["cards"][0] == "туз пентаклей", f"верная карта уничтожена: {data['cards']}"
    assert data["cards"][1] == "влюбленные"
    assert data["cards"][2] == "шут"
    assert data["bottom_card"] == "семь кубков"
    # «семь кубков» не задублировалось в cards (кражи соседа не было)
    assert "семь кубков" not in [normalize_card_input(c) for c in data["cards"]]


def test_spell_repaired_card_still_recovers_real_substitution():
    """Near-miss НЕ ломает ловлю подмены: «король жезлов» (подмена из «крыльево
    мячей») против сырого — на позиции стоит ДРУГАЯ резолвящаяся карта (Королева
    Мечей ≠ Король Жезлов) → _names_other_card=True → recover, не near-miss keep."""
    from miniapp.backend.tarot import normalize_card_input
    data = {"cards": ["туз пентакли", "влюбленные", "король жезлов"],
            "bottom_card": "семь кубков"}
    raw = "что чувствует туз пентакли влюбленные крыльево мячей дно семь кубков"
    ground_cards_in_data(data, raw, resolver=_waite_resolver())
    assert data["cards"][2] == "крыльево мячей", "подмену перестали ловить из-за near-miss"
    assert normalize_card_input(data["cards"][2]) == "королева мечей"


def test_nearmiss_does_not_keep_1word_hallucination_latching_common_word():
    """Регрессия (ревью near-miss): 1-словный мажор НЕ должен near-miss-держаться,
    залатчившись за случайное общее слово. Парсер выдумал «Колесница», в сыром —
    «клиента» (преамбула), score ~0.62 → раньше держал НЕназванную карту. Теперь
    k>=2 обязателен → recover к реально сказанному."""
    transcript = "расклад на клиента первая карта десятка мечей вторая туз кубков"
    out = ground_cards(["Колесница", "туз кубков"], transcript, resolver=_waite_resolver())
    assert out[0] != "Колесница", f"1-словная галлюцинация удержана near-miss: {out}"


def test_nearmiss_does_not_keep_gibberish_parser_card():
    """Регрессия (ревью near-miss): карта парсера — нераспознаваемый мусор
    («каралева меча»), а на позиции в сыром стоит реальная карта (королева мечей).
    _names_other_card должен вернуть True (cid=None) → recover, не держать мусор."""
    from miniapp.backend.tarot import normalize_card_input
    data = {"cards": ["туз пентаклей", "влюбленные", "шут", "каралева меча"]}
    raw = "туз пентаклей влюбленные шут королева мечей"
    ground_cards_in_data(data, raw, resolver=_waite_resolver())
    assert data["cards"][3] != "каралева меча", "мусорная карта удержана near-miss"
    assert normalize_card_input(data["cards"][3]) == "королева мечей"


def test_handle_add_session_grounds_against_ground_ref():
    """Source-guard: handle_add_session принимает ground_ref (сырой транскрипт) и
    грундит против него с фолбэком на text."""
    src = (REPO / "arcana" / "handlers" / "sessions.py").read_text(encoding="utf-8")
    assert 'ground_ref: str = ""' in src, "нет параметра ground_ref"
    assert "ground_ref or text" in src, "граундинг не использует сырой ref с фолбэком"


def test_raw_transcript_threaded_voice_to_grounding():
    """Source-guard: сырой Whisper пробрасывается голос → route_message →
    handle_add_session, захвачен ДО normalize_text."""
    bot_src = (REPO / "arcana" / "bot.py").read_text(encoding="utf-8")
    assert "_raw_transcript = text" in bot_src, "сырой транскрипт не захвачен в bot.py"
    assert "_raw=_raw_transcript" in bot_src, "_raw не проброшен в route_message"
    # захват ДО спелла: _raw_transcript присвоен раньше, чем normalize_text зовётся
    assert bot_src.index("_raw_transcript = text") < bot_src.index("normalize_text(text")
    base_src = (REPO / "arcana" / "handlers" / "base.py").read_text(encoding="utf-8")
    assert '_raw: str = ""' in base_src, "route_message не принимает _raw"
    assert "ground_ref=_raw" in base_src, "route_message не прокидывает ground_ref=_raw"
