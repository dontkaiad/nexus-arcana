"""tests/test_session_parser.py — устойчивость хендлера к форматам data dict.

Парсер Sonnet вызывает live LLM, поэтому здесь юнит-тесты на нашу
обвязку: _coerce_cards_str, SessionParseError, ветвление single/multi.
"""
from arcana.handlers.sessions import (
    _coerce_cards_str,
    _resolve_session_category,
    SessionParseError,
    PARSE_HELP_TEXT,
)


def test_coerce_cards_list():
    assert _coerce_cards_str(["шут", "маг", "жрица"]) == "шут, маг, жрица"


def test_coerce_cards_string():
    assert _coerce_cards_str("шут, маг, жрица") == "шут, маг, жрица"


def test_coerce_cards_none():
    assert _coerce_cards_str(None) == ""


def test_coerce_cards_dirty_list():
    # Пустые элементы и пробелы.
    assert _coerce_cards_str(["шут", "", "  ", "маг"]) == "шут, маг"


def test_resolve_category_by_name():
    # Имя человека → Сфера жизни (если контекст не явный)
    assert "🌐" in _resolve_session_category("Вадим", 3)
    assert "🌐" in _resolve_session_category("Маша", 2)


def test_resolve_category_work():
    assert _resolve_session_category("Работа", 4) in (
        "🌐 Сфера жизни",  # имя «работа» матчится по подстроке к «работа» в map
    )


def test_resolve_category_solo_default():
    assert _resolve_session_category(None, 1) == "🔺 Триплет"


def test_resolve_category_multi_default():
    assert _resolve_session_category(None, 3) == "🌐 Сфера жизни"


def test_session_parse_error_class():
    err = SessionParseError("test")
    assert isinstance(err, Exception)


def test_parse_help_text_contains_examples():
    assert "Вадим" in PARSE_HELP_TEXT
    assert "устроюсь" in PARSE_HELP_TEXT
    assert "<b>" in PARSE_HELP_TEXT  # HTML formatting for telegram
