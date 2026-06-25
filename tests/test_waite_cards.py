"""tests/test_waite_cards.py — жёсткий детерминированный парсер карт Уэйта.

ТОЛЬКО колода Уэйт (rider-waite). Авторские колоды идут прежним путём
(grounding + tarot_refs) — их тесты в test_card_grounding / test_arcana_mode_a_authored
должны остаться зелёными (этот файл их не трогает).

Покрываем:
  • состав 78 == реестр deck_cards.json (страж единого источника);
  • resolve_waite: старшие/младшие/фонетические мисхёрды/EN-вход/мусор→None;
  • членство: любой выход ∈ 78, мусор → None;
  • next_waite: скан сырого транскрипта, пропуск преамбулы, дно ≠ карта;
  • normalize_waite_cards_in_data: позиционное 1:1 (починка спелл-подмены),
    дно по маркеру, multi-триплеты, послотный фоллбэк + Haiku, мусор остаётся;
  • развилка в sessions.py: Уэйт → новый путь.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core import waite_cards as wc
from core.waite_cards import (
    WAITE_78_EN,
    classify_waite_card_llm,
    next_waite,
    normalize_waite_cards_in_data,
    resolve_waite,
)

REPO = Path(__file__).resolve().parent.parent


# ───────────────────── страж: 78 == реестр deck_cards.json ──────────────────

def test_waite_78_matches_registry_exactly():
    """Единый источник истины: hardcode-словарь 78 совпадает с реестром miniapp
    (по составу И порядку). Расходятся — кто-то правил одно без другого."""
    reg = json.loads((REPO / "deck_cards.json").read_text(encoding="utf-8"))
    reg_en = [c["en"] for c in reg["rider-waite"]["cards"]]
    assert len(WAITE_78_EN) == 78
    assert WAITE_78_EN == reg_en, (
        "состав/порядок 78 разошёлся с deck_cards.json — синхронизируй"
    )


def test_exactly_22_majors_and_56_minors():
    assert len(wc.MAJORS_EN) == 22
    assert len(wc.MINORS_EN) == 56
    # 4 масти × 14 рангов, без дублей; майоры и миноры не пересекаются
    assert len(set(wc.MINORS_EN)) == 56
    assert set(wc.MAJORS_EN).isdisjoint(wc.MINORS_EN)
    # "Wheel of Fortune"/"Justice"/… — старшие, даже если в имени есть "of"
    assert "Wheel of Fortune" in wc.MAJORS_EN


# ──────────────────────────── resolve_waite ────────────────────────────────

@pytest.mark.parametrize("phrase,expected", [
    # старшие — каноничный RU
    ("шут", "The Fool"),
    ("жрица", "The High Priestess"),
    ("верховная жрица", "The High Priestess"),
    ("колесо фортуны", "Wheel of Fortune"),
    ("повешенный", "The Hanged Man"),
    ("суд", "Judgement"),
    ("мир", "The World"),
    ("влюбленные", "The Lovers"),     # ё→е
    ("влюблённые", "The Lovers"),
    # младшие — слова-ранги
    ("король кубков", "King of Cups"),
    ("королева мечей", "Queen of Swords"),
    ("семь кубков", "Seven of Cups"),
    ("семёрка пентаклей", "Seven of Pentacles"),
    ("десятка жезлов", "Ten of Wands"),
    # младшие — цифры
    ("9 пентаклей", "Nine of Pentacles"),
    ("2 мечей", "Two of Swords"),
    ("10 жезлов", "Ten of Wands"),
    # фонетические мисхёрды Whisper
    ("крыльева мячей", "Queen of Swords"),   # королева + мечей
    ("туз мячей", "Ace of Swords"),
    ("каралева мячи", "Queen of Swords"),
    # EN-вход идемпотентен
    ("Queen of Swords", "Queen of Swords"),
    ("Seven of Cups", "Seven of Cups"),
    ("The Hanged Man", "The Hanged Man"),
    ("ace of wands", "Ace of Wands"),
    # синонимы масти
    ("туз монет", "Ace of Pentacles"),
    ("король чаш", "King of Cups"),
    ("король посохов", "King of Wands"),
])
def test_resolve_waite_known(phrase, expected):
    assert resolve_waite(phrase) == expected


@pytest.mark.parametrize("phrase", [
    "", "  ", "дно", "что чувствует", "на ценностях", "клиент я",
    "колода уэйт", "расклад на работу", "туз", "король", "просто текст",
    "семь", "пентакли",
])
def test_resolve_waite_non_cards_return_none(phrase):
    assert resolve_waite(phrase) is None


def test_every_resolution_is_member_of_78():
    """Любой ненулевой выход — точный член 78, не «похоже»."""
    samples = ["шут", "крыльева мячей", "9 пентаклей", "король кубков",
               "колесо фортуны", "Queen of Swords", "туз монет"]
    for s in samples:
        out = resolve_waite(s)
        assert out is None or out in set(WAITE_78_EN)


# ──────────────────────────── next_waite скан ──────────────────────────────

def test_next_waite_skips_preamble_and_advances():
    toks = ("расклад на работу колода уэйта что чувствует "
            "крыльева мячей шут жрица").split()
    en1, c1 = next_waite(toks, 0)
    assert en1 == "Queen of Swords"           # преамбула/вопрос пропущены
    en2, c2 = next_waite(toks, c1)
    assert en2 == "The Fool"
    en3, c3 = next_waite(toks, c2)
    assert en3 == "The High Priestess"
    en4, _ = next_waite(toks, c3)
    assert en4 is None                          # карты кончились


def test_next_waite_bottom_marker_is_not_a_card():
    toks = "король кубков дно семь пентаклей".split()
    en1, c1 = next_waite(toks, 0)
    assert en1 == "King of Cups"
    en2, _ = next_waite(toks, c1)
    assert en2 == "Seven of Pentacles"          # «дно» пропущено, не карта


def test_next_waite_prefers_longer_major():
    toks = "колесо фортуны".split()
    en, c = next_waite(toks, 0)
    assert en == "Wheel of Fortune" and c == 2


# ───────────────── normalize_waite_cards_in_data: позиционное 1:1 ───────────

async def test_positional_recovers_spell_swap_single():
    """Голос: спелл подменил «крыльева мячей» → «король жезлов» (валидная-но-чужая)
    ещё ДО парсера. Сырой транскрипт — истина: позиционно 1:1 чинит на Queen of
    Swords. Выход — canonical EN."""
    raw = "что человек чувствует крыльева мячей шут жрица дно король кубков"
    data = {
        "cards": ["король жезлов", "шут", "жрица"],   # parser-подмена в [0]
        "bottom_card": "король кубков",
    }
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Queen of Swords", "The Fool", "The High Priestess"]
    assert data["bottom_card"] == "King of Cups"


async def test_positional_no_bottom_when_no_marker():
    raw = "туз мечей двойка кубков влюбленные"
    data = {"cards": ["туз мечей", "двойка кубков", "влюбленные"],
            "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Ace of Swords", "Two of Cups", "The Lovers"]
    assert not data.get("bottom_card")


async def test_positional_multi_triplets():
    raw = (
        "что думает король кубков туз мечей девять пентаклей дно двойка мечей "
        "что чувствует семь мечей восемь жезлов колесо фортуны дно двойка жезлов"
    )
    data = {"triplets": [
        {"cards": ["король кубков", "туз мечей", "девять пентаклей"],
         "bottom_card": "двойка мечей"},
        {"cards": ["семь мечей", "восемь жезлов", "колесо фортуны"],
         "bottom_card": "двойка жезлов"},
    ]}
    await normalize_waite_cards_in_data(data, raw)
    assert data["triplets"][0]["cards"] == [
        "King of Cups", "Ace of Swords", "Nine of Pentacles"]
    assert data["triplets"][0]["bottom_card"] == "Two of Swords"
    assert data["triplets"][1]["cards"] == [
        "Seven of Swords", "Eight of Wands", "Wheel of Fortune"]
    assert data["triplets"][1]["bottom_card"] == "Two of Wands"


# ───────────── adversarial: фантомы нарратива / порядок дна ─────────────────

async def test_narrative_phantom_does_not_shift_or_drop_bottom():
    """Finding 1: нарратив «десять монет» складывается в фантом Ten of Pentacles.
    Якорь-подтверждение по КОНКРЕТНОЙ карте слота фантом игнорирует — карты не
    сдвигаются, дно (Дьявол, его нет в сыром) сохраняется."""
    raw = "смотрю на десять монет сверху лежит маг снизу шут"
    data = {"cards": ["Маг", "Шут"], "bottom_card": "Дьявол"}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["The Magician", "The Fool"]
    assert data["bottom_card"] == "The Devil"     # НЕ потеряно, НЕ сдвинуто


async def test_bottom_marker_spoken_before_last_card():
    """Finding 2: «дно» названо ДО последней карты стола (живая речь). Карта 3 и
    дно НЕ должны меняться местами."""
    raw = "туз кубков сила дно колоды шут башня"
    data = {"cards": ["Туз Кубков", "Сила", "Башня"], "bottom_card": "Шут"}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Ace of Cups", "Strength", "The Tower"]
    assert data["bottom_card"] == "The Fool"


async def test_spell_swap_recovered_despite_preamble_phantom():
    """Резидуал: испорченный слот 0 + фантом в преамбуле («за десять монет»).
    Восстанавливаем реальную крыльева мячей→Queen of Swords (рядом с соседями),
    фантом Ten of Pentacles из преамбулы отбрасывается."""
    raw = "за десять монет крыльева мячей шут жрица"
    data = {"cards": ["король жезлов", "шут", "жрица"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Queen of Swords", "The Fool", "The High Priestess"]


async def test_money_reading_phantoms_do_not_corrupt():
    """Денежный расклад: «за пять монет» и «три чаши» в вопросах/нарративе —
    фантомы. Реальные карты подтверждаются по имени, фантомы игнорируются."""
    raw = ("вопрос про работу за пять монет туз жезлов колесница император "
           "потом три чаши на столе")
    data = {"cards": ["Туз Жезлов", "Колесница", "Император"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Ace of Wands", "The Chariot", "The Emperor"]


async def test_trailing_narrative_phantom_does_not_steal_swapped_last_card():
    """Re-review HIGH (хвостовая дырка): ПОСЛЕДНЯЯ карта спелл-подменена («крыльева
    мячей»→спелл→«король жезлов»), нарратив ПОСЛЕ неё называет карту («башня»).
    Реальная карта в голове дырки, фантом в хвосте — позиционный хвост-pick брал
    бы фантом. Близость фразы к спану тянет к истоку: slot 3 → Queen of Swords."""
    raw = "маг жрица крыльева мячей башня"
    data = {"cards": ["маг", "жрица", "король жезлов"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["The Magician", "The High Priestess", "Queen of Swords"]


async def test_novel_slot_between_anchors_goes_to_haiku_no_steal():
    """Novel-мисхёрд (сырьё его тоже не разбирает) между двумя верными картами:
    слот не крадёт соседний спан, идёт в Haiku; якоря целы."""
    raw = "туз кубков тарабарщина жрица"
    data = {"cards": ["туз кубков", "тарабарщина", "жрица"], "bottom_card": None}

    async def fake_llm(phrase):
        return None                            # Haiku тоже не узнал
    with patch.object(wc, "classify_waite_card_llm", side_effect=fake_llm):
        await normalize_waite_cards_in_data(data, raw)
    assert data["cards"][0] == "Ace of Cups"
    assert data["cards"][1] == "тарабарщина"   # novel остался дословно, не украл спан
    assert data["cards"][2] == "The High Priestess"


async def test_phantoms_between_anchored_cards_dropped():
    """Re-review HIGH (инвариант): верные карты звучат в сыром → становятся
    ЯКОРЯМИ. Фантомы нарратива МЕЖДУ ними («десять монет», «три чаши») — в дырках
    с 0 слотов → отбрасываются, не затирают якорь."""
    raw = "туз кубков десять монет сила три чаши башня"
    data = {"cards": ["Туз Кубков", "Сила", "Башня"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Ace of Cups", "Strength", "The Tower"]


async def test_trailing_narrative_phantom_after_all_anchors_dropped():
    """Фантомы в нарративе ПОСЛЕ всех карт (триплет назван, дальше разбор) —
    дырка без слотов → отброшены."""
    raw = "маг жрица император потом десять монет и три чаши в разговоре"
    data = {"cards": ["Маг", "Жрица", "Император"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["The Magician", "The High Priestess", "The Emperor"]


async def test_preamble_phantom_then_all_anchored_cards():
    """Фантом в преамбуле ПЕРЕД верными картами → дырка перед первым якорем без
    слотов → отброшен; все карты-якоря целы."""
    raw = "за десять монет расклад туз жезлов колесница император"
    data = {"cards": ["Туз Жезлов", "Колесница", "Император"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Ace of Wands", "The Chariot", "The Emperor"]


async def test_same_suit_court_phantom_does_not_steal_swapped_slot():
    """Re-review HIGH (структурная дыра surface-похожести): нарратив называет
    карту, ШАРЯЩУЮ масть/ранг с подменённой фразой слота («король кубков» vs
    «король жезлов» — общий «король», sim 0.67 > истока 0.38). Чистый фантом
    мисхёрда не содержит → не eligible; восстановление идёт из «грязного»
    «крыльева мячей» → Queen of Swords."""
    raw = ("вижу маг потом жрица а тут король кубков как мужчина "
           "и финал крыльева мячей")
    data = {"cards": ["маг", "жрица", "король жезлов"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["The Magician", "The High Priestess", "Queen of Swords"]


async def test_clean_card_in_narrative_not_recovered_into_swapped_slot():
    """Подмена есть, но истока-мисхёрда в дырке НЕТ (только чистый фантом) → слот
    держит enP парсера, чистый фантом нарратива НЕ подставляется."""
    raw = "маг жрица потом король кубков в разговоре про мужчину"
    data = {"cards": ["маг", "жрица", "король жезлов"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    # нет грязного истока → slot3 держит enP (King of Wands), фантом не украл
    assert data["cards"] == ["The Magician", "The High Priestess", "King of Wands"]


async def test_two_adjacent_spell_swaps_recovered_by_span_similarity():
    """Две подмены подряд (реалистично: спелл исказил написание, не до
    неузнаваемости) → каждая тянется к своему истоку по близости спана."""
    # raw: туз мячей + крыльева мячей; спелл → парсер «туз жезлов», «король жезлов»
    raw = "туз мячей крыльева мячей жрица"
    data = {"cards": ["туз жезлов", "король жезлов", "жрица"], "bottom_card": None}
    await normalize_waite_cards_in_data(data, raw)
    assert data["cards"] == ["Ace of Swords", "Queen of Swords", "The High Priestess"]


# ───────────── послотный фоллбэк (счёт разошёлся) + Haiku ───────────────────

async def test_per_slot_deterministic_when_no_raw():
    """Текст без сырого совпадения по счёту → послотный resolve_waite, без LLM."""
    data = {"cards": ["король кубков", "9 пентаклей", "жрица"], "bottom_card": None}
    with patch.object(wc, "classify_waite_card_llm") as llm:
        await normalize_waite_cards_in_data(data, "")   # raw пуст → счёт разошёлся
        llm.assert_not_called()
    assert data["cards"] == ["King of Cups", "Nine of Pentacles", "The High Priestess"]


async def test_per_slot_haiku_fallback_for_novel_mishear():
    """Незнакомый мисхёрд: deterministic=None → Haiku. Счёт расходится (raw не даёт
    эту карту), идём послотно; LLM зовётся только на неразобранный слот."""
    data = {"cards": ["король кубков", "жрица", "квазимба мечкинская"],
            "bottom_card": None}

    async def fake_llm(phrase):
        return "Knight of Swords" if "квазимба" in phrase else None

    # raw содержит только 2 распознаваемых → счёт (2) != слотов (3) → послотно
    with patch.object(wc, "classify_waite_card_llm", side_effect=fake_llm) as llm:
        await normalize_waite_cards_in_data(data, "король кубков жрица")
    assert data["cards"] == ["King of Cups", "The High Priestess", "Knight of Swords"]
    llm.assert_awaited_once()


async def test_per_slot_unresolved_stays_raw_for_correction():
    """deterministic=None и LLM=None → слот остаётся дословным (Кай поправит)."""
    data = {"cards": ["король кубков", "абракадабра"], "bottom_card": None}

    async def fake_llm(phrase):
        return None

    with patch.object(wc, "classify_waite_card_llm", side_effect=fake_llm):
        await normalize_waite_cards_in_data(data, "")
    assert data["cards"][0] == "King of Cups"
    assert data["cards"][1] == "абракадабра"      # сохранён дословно


# ───────────────────── classify_waite_card_llm: гард членства ───────────────

async def test_llm_rejects_non_member_output():
    """Haiku вернул не-карту → None (не принимаем «похоже»)."""
    async def fake_ask(*a, **k):
        return "Some Nonsense Card"
    with patch("core.claude_client.ask_claude", side_effect=fake_ask):
        assert await classify_waite_card_llm("кривая фраза") is None


async def test_llm_accepts_member_and_maps_ru():
    async def fake_ask_en(*a, **k):
        return "Queen of Swords"
    with patch("core.claude_client.ask_claude", side_effect=fake_ask_en):
        assert await classify_waite_card_llm("кривая фраза") == "Queen of Swords"

    async def fake_ask_ru(*a, **k):
        return "королева мечей"        # модель ответила RU → резолвер дотянет
    with patch("core.claude_client.ask_claude", side_effect=fake_ask_ru):
        assert await classify_waite_card_llm("кривая фраза") == "Queen of Swords"


async def test_llm_null_returns_none():
    async def fake_ask(*a, **k):
        return "null"
    with patch("core.claude_client.ask_claude", side_effect=fake_ask):
        assert await classify_waite_card_llm("шум") is None


# ───────────────────────── развилка в sessions.py ──────────────────────────

def test_sessions_forks_waite_to_hard_parser():
    """Source-guard: handle_add_session разводит по колоде — Уэйт зовёт новый
    жёсткий парсер, авторские остаются на grounding."""
    src = (REPO / "arcana" / "handlers" / "sessions.py").read_text(encoding="utf-8")
    i_parse = src.index("system=PARSE_SESSION_SYSTEM")
    i_fork = src.index('if _gr_deck == "rider-waite":')
    i_waite = src.index("normalize_waite_cards_in_data(data")
    i_ground = src.index("ground_cards_in_data(")
    i_multi = src.index("_handle_multi_session(")
    # развилка после парса и до split single/multi (покрывает оба флоу)
    assert i_parse < i_fork < i_multi
    assert i_fork < i_waite < i_multi
    # авторская ветка (grounding) сохранена
    assert i_fork < i_ground
