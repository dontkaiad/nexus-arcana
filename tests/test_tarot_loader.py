"""tests/test_tarot_loader.py — get_cards_context: reversed-значения фильтруются.

Гарантия: ни одна из колод (Уэйт, Dark Wood, Deviant Moon, игральные) не попадает
в LLM-промпт с reversed-значением карты — источник галлюцинации «перевёрнутое положение»
в режиме A (ADR-0017).
"""
import pytest
from arcana.tarot_loader import get_cards_context


def test_waite_no_reversed_key_in_context():
    ctx = get_cards_context("Уэйт", ["Четвёрка Кубков"])
    # up-значение присутствует
    assert "Апатия" in ctx
    # ключ rev и reversed-текст отсутствуют
    assert "  rev:" not in ctx
    assert "Выход из апатии" not in ctx


def test_dark_wood_no_reversed_key_in_context():
    ctx = get_cards_context("Dark Wood", ["Четвёрка Кубков"])
    assert "перевёрнутая:" not in ctx


def test_deviant_moon_no_reversed_key_in_context():
    ctx = get_cards_context("Deviant Moon", ["Четверка Кубков"])
    assert "перевёрнутое:" not in ctx
    # само reversed-значение тоже не утекает
    assert "Депрессия" not in ctx


def test_playing_cards_no_reversed_key_in_context():
    ctx = get_cards_context("игральные", ["6 Пик"])
    assert "  rev:" not in ctx
    # reversed-текст игральных не попадает
    assert "Нежелательный переезд" not in ctx
