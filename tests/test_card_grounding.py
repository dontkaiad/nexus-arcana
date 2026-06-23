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
    i_ground = src.index("ground_cards_in_data(data, text)")
    i_multi = src.index("_handle_multi_session(")
    assert i_parse < i_ground < i_multi, "граундинг должен быть после парса и до multi-split"
