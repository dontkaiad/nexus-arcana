"""tests/test_arcana_session_figurant.py — #85: словарь кодовых слов
(категория 🎭 Фигуранты) вливается в промпт Arcana-парсера сессий.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.repos.pg_memory_repo import Memory


def _fig(fact: str, key: str) -> Memory:
    return Memory(id=key, fact=fact, key=key, category="🎭 Фигуранты",
                  scope="arcana", user_id="u")


# ── get_figurant_dictionary ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_figurant_dictionary_builds_block():
    import core.memory as mem
    rows = [
        _fig("корабль = объект приворота клиентки Оли, реальное имя Пётр", "корабль"),
        _fig("дом = квартира мамы", "дом"),
    ]
    with patch.object(mem, "_mem_repo") as repo:
        repo.find_by_category = AsyncMock(return_value=rows)
        block = await mem.get_figurant_dictionary("u")

    repo.find_by_category.assert_awaited_once()
    assert "СЛОВАРЬ КОДОВЫХ СЛОВ" in block
    assert "корабль = объект приворота клиентки Оли" in block
    assert "дом = квартира мамы" in block
    # правило про session_name
    assert "session_name" in block
    assert "session_category" in block


@pytest.mark.asyncio
async def test_figurant_dictionary_empty_when_no_rows():
    import core.memory as mem
    with patch.object(mem, "_mem_repo") as repo:
        repo.find_by_category = AsyncMock(return_value=[])
        assert await mem.get_figurant_dictionary("u") == ""


@pytest.mark.asyncio
async def test_figurant_dictionary_empty_on_repo_error():
    import core.memory as mem
    with patch.object(mem, "_mem_repo") as repo:
        repo.find_by_category = AsyncMock(side_effect=RuntimeError("pg down"))
        assert await mem.get_figurant_dictionary("u") == ""


@pytest.mark.asyncio
async def test_figurant_dictionary_no_user_id():
    import core.memory as mem
    assert await mem.get_figurant_dictionary("") == ""


# ── handle_add_session вливает словарь в system-промпт ────────────────────────

_GOOD_JSON = json.dumps({
    "session_name": "Оля — корабль (Пётр)",
    "session_category": "Магические воздействия",
    "client_name": "оля",
    "triplets": [{"question": "состояние корабля", "cards": ["шут", "маг", "жрица"],
                  "bottom_card": None, "area": "Общая ситуация",
                  "spread_type": "Триплет", "interpretation": None}],
    "amount": 0, "paid": 0, "payment_source": None, "deck": "Уэйт",
})


def _session_msg():
    bot_msg = MagicMock()
    bot_msg.chat.id = 100
    bot_msg.message_id = 555
    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock(return_value=bot_msg)
    return msg


@pytest.mark.asyncio
async def test_handle_add_session_appends_figurant_dict_to_system():
    import arcana.handlers.sessions as sess

    ask = AsyncMock(side_effect=[_GOOD_JSON, "общий вывод"])
    facts = ["корабль = объект приворота клиентки Оли, реальное имя Пётр"]
    with patch("arcana.handlers.sessions.ask_claude", ask), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch("core.memory.get_figurant_facts", AsyncMock(return_value=facts)), \
         patch("arcana.handlers.sessions._handle_multi_session", AsyncMock()):
        await sess.handle_add_session(_session_msg(), "оля состояние корабля", user_id="u")

    first_call = ask.await_args_list[0]
    system = first_call.kwargs.get("system") or first_call.args[1]
    assert system.startswith(sess.PARSE_SESSION_SYSTEM)
    assert "СЛОВАРЬ КОДОВЫХ СЛОВ" in system
    assert "реальное имя Пётр" in system


@pytest.mark.asyncio
async def test_handle_add_session_no_figurants_system_unchanged():
    import arcana.handlers.sessions as sess

    ask = AsyncMock(side_effect=[_GOOD_JSON, "общий вывод"])
    with patch("arcana.handlers.sessions.ask_claude", ask), \
         patch("arcana.handlers.sessions.get_user_tz", AsyncMock(return_value=3)), \
         patch("core.memory.get_figurant_facts", AsyncMock(return_value=[])), \
         patch("arcana.handlers.sessions._handle_multi_session", AsyncMock()):
        await sess.handle_add_session(_session_msg(), "оля состояние корабля", user_id="u")

    first_call = ask.await_args_list[0]
    system = first_call.kwargs.get("system") or first_call.args[1]
    assert system == sess.PARSE_SESSION_SYSTEM


# ── extract_context_keywords тянет вопросы триплетов (для Sonnet-контекста) ────

def test_extract_context_keywords_includes_triplet_questions():
    from core.memory import extract_context_keywords
    data = {
        "session_name": "Оля — корабль",
        "subject_name": "Пётр",
        "triplets": [{"question": "состояние корабля сейчас"}],
    }
    kw = extract_context_keywords(data)
    assert "Оля — корабль" in kw
    assert "Пётр" in kw
    assert "корабля" in kw  # слово из вопроса триплета
