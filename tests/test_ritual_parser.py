"""tests/test_ritual_parser.py — toleranсе к опечаткам и clarification flow."""
from arcana.handlers.rituals import PARSE_RITUAL_SYSTEM, CLARIFICATION_TEXT


def test_prompt_lists_typo_tolerance():
    """Промпт явно перечисляет опечатки, чтобы Sonnet их канонизировал."""
    s = PARSE_RITUAL_SYSTEM
    assert "финансковый" in s
    assert "люберый" in s
    assert "очистка" in s
    assert "разрыв" in s


def test_prompt_demands_needs_clarification_field():
    s = PARSE_RITUAL_SYSTEM
    assert "needs_clarification" in s
    assert "true|false" in s


def test_clarification_text_helpful():
    """Текст подсказки действительно подсказывает что нужно: силы, структура, …"""
    t = CLARIFICATION_TEXT
    assert "силы" in t.lower()
    assert "структур" in t.lower()
    assert "расходник" in t.lower() or "подношен" in t.lower()
