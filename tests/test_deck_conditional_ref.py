"""tests/test_deck_conditional_ref.py — правило справочника deck-conditional.

Уэйт (классическая) — Sonnet знает канон, справочник опционален, missing-карту
раскрывает по классике. Авторские колоды (Dark Wood, Deviant Moon, Ленорман,
игральные) — значения СТРОГО из справочника, классику подставлять НЕЛЬЗЯ (у них
свои нестандартные смыслы). Имя колоды доезжает в промпт даже когда карт нет.
"""
from __future__ import annotations

import pytest

from arcana.handlers.sessions import TAROT_SYSTEM, PERSONAL_INTERP_SYSTEM
from arcana.tarot_loader import get_cards_context


@pytest.mark.parametrize("prompt,label", [
    (TAROT_SYSTEM, "режим B"),
    (PERSONAL_INTERP_SYSTEM, "режим A"),
])
def test_prompt_is_deck_conditional(prompt, label):
    """Оба режима: правило про справочник зависит от колоды (Уэйт vs авторская)."""
    p = prompt
    assert "ЗАВИС" in p and "КОЛОД" in p.upper(), f"{label}: правило не deck-conditional"
    # Уэйт-ветка: канон, missing не мешает
    assert "Уэйт" in p
    assert "классическ" in p.lower(), f"{label}: нет ветки классического значения Уэйта"
    assert "НЕ мешает" in p, f"{label}: missing-карта Уэйта должна раскрываться по канону"


@pytest.mark.parametrize("prompt,label", [
    (TAROT_SYSTEM, "режим B"),
    (PERSONAL_INTERP_SYSTEM, "режим A"),
])
def test_authored_decks_strict_rule_intact(prompt, label):
    """Авторские колоды — строгое правило ЦЕЛО: только справочник, классику НЕ
    подставлять (иначе Sonnet подменит dark_wood-значения классикой)."""
    p = prompt
    # перечислены конкретные авторские колоды
    assert "Dark Wood" in p and "Deviant Moon" in p, f"{label}: нет авторских колод в правиле"
    # строго из справочника
    assert "СТРОГО из справочника" in p or "ТОЛЬКО значения из справочника" in p, \
        f"{label}: ослаблено строгое правило авторских"
    # явный запрет подставлять классику для авторских
    assert "классику" in p.lower() or "классические значения" in p.lower(), \
        f"{label}: нет запрета подставлять классику для авторских"


def test_deck_name_reaches_prompt_even_when_all_cards_missing():
    """Имя колоды отдаётся в контекст даже когда ни одна карта не нашлась —
    иначе deck-conditional правило не сработало бы на missing."""
    ctx = get_cards_context("Уэйт", ["крокозябранесуществующая"])
    assert ctx, "пустой контекст → модель не узнает колоду"
    assert "Колода: Уэйт" in ctx


def test_found_card_still_returns_meanings():
    """Регресс: найденная карта по-прежнему даёт заголовок + значение."""
    ctx = get_cards_context("Уэйт", ["Туз Мечей"])
    assert "Колода: Уэйт" in ctx
    assert "📍" in ctx and "меч" in ctx.lower()


def test_unknown_deck_returns_empty():
    """Неизвестная колода (нет файла) → пусто, правило не нужно."""
    assert get_cards_context("НетТакойКолоды", ["Туз Мечей"]) == ""
