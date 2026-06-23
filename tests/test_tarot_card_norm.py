"""tests/test_tarot_card_norm.py — нормализация имён карт из ГОЛОСА.

Парсер из речи (режим A) отдаёт именительную масть («Пентакли», «Кубки») и
количественный ранг («Шесть», «Тройка»), а waite.json/реестр хранят родительный
(«Пентаклей», «Кубков») + цифру/порядковое. Нормализатор приводит речь к канону,
иначе get_cards_context не находит значения карт → card_not_in_ref в мониторинг.

Регрессия: ранее-работавшие формы (родительный/цифра/порядковое/двор) тоже резолвятся.
"""
from __future__ import annotations

import pytest

from miniapp.backend.tarot import normalize_card_input
from arcana.tarot_loader import (
    _lookup_card,
    _load_deck,
    get_cards_context,
    get_deck_file,
    missing_cards,
)

DECK = "Уэйт"

# Ровно те 7 имён, что сыпали card_not_in_ref (recon).
VOICE_FAILED = [
    "Туз Пентакли", "Пятёрка Пентакли", "Шесть Кубков", "Тройка Пентакли",
    "Семёрка Пентакли", "Паж Пентакли", "Король Пентакли",
]

# Ранее-работавшие формы — не должны сломаться.
CONTROL_OK = [
    "Туз Пентаклей", "5 Пентаклей", "6 Кубков", "Шестёрка Кубков",
    "9 пентаклей", "Король Кубков",
]


def _deck():
    return _load_deck(get_deck_file(DECK))


@pytest.mark.parametrize("card", VOICE_FAILED)
def test_voice_nominative_cards_now_resolve(card):
    """Каждая из 7 ранее-падавших карт находится в справочнике waite."""
    assert _lookup_card(_deck(), DECK, card), f"{card!r} не нашлась в справочнике"


# Фонетические мисхёрды Whisper (мечей↔мячей, королева↔крыльева) → канон.
WHISPER_MISHEARS = [
    ("туз мячей", "туз мечей"),
    ("шесть мячей", "шестёрка мечей"),
    ("крыльева мячей", "королева мечей"),   # двойное искажение (recon-кейс)
    ("крыльева мечей", "королева мечей"),
    ("кралева кубков", "королева кубков"),
]


@pytest.mark.parametrize("raw,canon", WHISPER_MISHEARS)
def test_whisper_mishears_normalize(raw, canon):
    from miniapp.backend.tarot import normalize_card_input
    assert normalize_card_input(raw) == canon


@pytest.mark.parametrize("raw", [m[0] for m in WHISPER_MISHEARS])
def test_whisper_mishears_resolve_in_deck(raw):
    assert _lookup_card(_deck(), DECK, raw), f"мисхёрд {raw!r} не сматчился"


@pytest.mark.parametrize("card", CONTROL_OK)
def test_previously_working_forms_still_resolve(card):
    """Регресс: родительный/цифра/порядковое/двор всё ещё резолвятся."""
    assert _lookup_card(_deck(), DECK, card), f"регресс: {card!r} перестала находиться"


def test_missing_cards_empty_for_voice_forms():
    """missing_cards пуст — ни одна голосовая форма не выпадает из справочника."""
    assert missing_cards(DECK, VOICE_FAILED) == []


def test_get_cards_context_returns_meanings():
    """Справочник реально отдаёт значения этих карт (не пустой контекст)."""
    ctx = get_cards_context(DECK, VOICE_FAILED)
    assert ctx
    # значения каждой запрошенной карты присутствуют (📍 <имя>)
    for card in VOICE_FAILED:
        assert f"📍 {card}" in ctx


@pytest.mark.parametrize("raw,canon", [
    # именительная масть → родительный канон
    ("туз пентакли", "туз пентаклей"),
    ("паж пентакли", "паж пентаклей"),
    ("король пентакли", "король пентаклей"),
    # количественный ранг → порядковое (масть уже родительная)
    ("шесть кубков", "шестёрка кубков"),
    ("три кубков", "тройка кубков"),
    # количественный ранг + именительная масть (оба чинятся)
    ("семь пентакли", "семёрка пентаклей"),
    ("восемь мечи", "восьмёрка мечей"),
    ("девять жезлы", "девятка жезлов"),
    # уже-канон формы остаются собой
    ("9 пентаклей", "девятка пентаклей"),
    ("шестёрка кубков", "шестёрка кубков"),
])
def test_normalize_nominative_and_cardinal(raw, canon):
    assert normalize_card_input(raw) == canon


@pytest.mark.parametrize("text", ["шут", "влюблённые", "the fool", "королева кубков"])
def test_non_card_or_court_inputs_unaffected(text):
    """Одиночные имена/Старшие Арканы/двор не ломаются нормализатором."""
    out = normalize_card_input(text)
    # старшие/одиночные возвращаются как есть (lower), двор — резолвится дальше
    assert out
    if text == "шут":
        assert out == "шут"
