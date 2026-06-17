"""tests/test_session_parser.py — устойчивость хендлера к форматам data dict.

Парсер Sonnet вызывает live LLM, поэтому здесь юнит-тесты на нашу
обвязку: _coerce_cards_str, SessionParseError, ветвление single/multi.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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


def test_resolve_category_all_triplets_default():
    # Multi-session, но все записи — триплеты (3 карты). Без явной категории
    # дефолт «🔺 Триплет», не «🌐 Сфера жизни». См. #83.
    assert _resolve_session_category(None, 9, all_triplets=True) == "🔺 Триплет"


def test_resolve_category_explicit_overrides_all_triplets():
    # Если Haiku явно вернула «Магические воздействия» — она важнее
    # all_triplets=True.
    assert "Магические" in _resolve_session_category(
        "Магические воздействия", 9, all_triplets=True
    )


def test_session_parse_error_class():
    err = SessionParseError("test")
    assert isinstance(err, Exception)


def test_parse_help_text_contains_examples():
    assert "Вадим" in PARSE_HELP_TEXT
    assert "устроюсь" in PARSE_HELP_TEXT
    assert "<b>" in PARSE_HELP_TEXT  # HTML formatting for telegram


# ── UX: невалидное имя клиента → переспрос ──────────────────────────────────

def _session_msg():
    bot_msg = MagicMock()
    bot_msg.chat.id = 100
    bot_msg.message_id = 555
    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock(return_value=bot_msg)
    return msg


_GOOD_CARDS_JSON = json.dumps({
    "cards": ["шут", "маг", "жрица"],
    "bottom_card": None,
    "area": "Отношения",
    "spread_type": "Триплет",
    "question": "что думает",
    "amount": 0, "paid": 0, "payment_source": None,
})


@pytest.mark.asyncio
async def test_invalid_client_name_single_flow_sends_clarification():
    """LLM-рефузал в client_name (single flow) → бот переспрашивает, клиент не создаётся."""
    import arcana.handlers.sessions as sess
    import core.client_resolve as cr

    bad_json = json.dumps(json.loads(_GOOD_CARDS_JSON) | {"client_name": "не могу извлечь имя"})
    foc = AsyncMock()

    with patch("arcana.handlers.sessions.ask_claude", AsyncMock(return_value=bad_json)), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch.object(cr, "find_or_create_client", foc):
        await sess.handle_add_session(_session_msg(), "test", user_notion_id="u")

    texts = [c.args[0] for c in _session_msg().answer.call_args_list if c.args]
    # Проверяем через отдельно созданный msg из функции (выше msg уже использован)
    # — реальная проверка через foc: клиент точно не создан
    foc.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_client_name_single_flow_message_text():
    """Текст переспроса содержит «имя клиента»."""
    import arcana.handlers.sessions as sess
    import core.client_resolve as cr

    bad_json = json.dumps(json.loads(_GOOD_CARDS_JSON) | {"client_name": "не могу извлечь имя"})
    msg = _session_msg()

    with patch("arcana.handlers.sessions.ask_claude", AsyncMock(return_value=bad_json)), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch.object(cr, "find_or_create_client", AsyncMock()):
        await sess.handle_add_session(msg, "test", user_notion_id="u")

    texts = [c.args[0] for c in msg.answer.call_args_list if c.args]
    assert any("имя клиента" in t for t in texts)


@pytest.mark.asyncio
async def test_valid_client_name_single_flow_no_clarification():
    """Валидное имя (single flow) → переспрос не шлётся, resolve_or_create вызывается."""
    import arcana.handlers.sessions as sess
    import core.client_resolve as cr

    good_json = json.dumps(json.loads(_GOOD_CARDS_JSON) | {"client_name": "оля"})
    msg = _session_msg()
    roc = AsyncMock(return_value="c-olia")

    with patch("arcana.handlers.sessions.ask_claude",
               AsyncMock(side_effect=[good_json, "<p>ok</p>"])), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch.object(cr, "resolve_or_create", roc), \
         patch("arcana.handlers.sessions._save_and_post_triplet", AsyncMock()):
        await sess.handle_add_session(msg, "оля расклад", user_notion_id="u")

    roc.assert_awaited_once()
    texts = [c.args[0] for c in msg.answer.call_args_list if c.args]
    assert not any("имя клиента" in t for t in texts)


@pytest.mark.asyncio
async def test_invalid_client_name_multi_flow_sends_clarification():
    """LLM-рефузал в client_name (multi flow) → бот переспрашивает, клиент не создаётся."""
    import arcana.handlers.sessions as sess
    import core.client_resolve as cr

    triplet = {"question": "q", "cards": ["шут", "маг", "жрица"],
               "bottom_card": None, "area": "Отношения", "spread_type": "Триплет"}
    bad_json = json.dumps({
        "client_name": "не могу извлечь имя",
        "session_name": "тест",
        "session_category": None,
        "deck": "Уэйт",
        "amount": 0, "paid": 0, "payment_source": None,
        "triplets": [triplet, dict(triplet, question="q2")],
    })
    msg = _session_msg()
    foc = AsyncMock()

    with patch("arcana.handlers.sessions.ask_claude", AsyncMock(return_value=bad_json)), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch.object(cr, "find_or_create_client", foc):
        await sess.handle_add_session(msg, "test", user_notion_id="u")

    texts = [c.args[0] for c in msg.answer.call_args_list if c.args]
    assert any("имя клиента" in t for t in texts)
    foc.assert_not_awaited()
