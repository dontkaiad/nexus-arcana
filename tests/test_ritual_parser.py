"""tests/test_ritual_parser.py — toleranсе к опечаткам и clarification flow."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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


# ── PgRitualsRepo unit tests ──────────────────────────────────────────────────

def test_pg_rituals_repo_find_by_id_invalid_id():
    """_find_by_id_sync возвращает None для нечисловых ID (без DB)."""
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    repo = PgRitualsRepo()
    assert repo._find_by_id_sync("not-an-int") is None
    assert repo._find_by_id_sync("") is None
    assert repo._find_by_id_sync(None) is None


def test_pg_rituals_repo_update_photo_url_invalid_id():
    """_update_photo_url_sync возвращает False для нечисловых ID (без DB)."""
    from arcana.repos.pg_rituals_repo import PgRitualsRepo
    repo = PgRitualsRepo()
    assert repo._update_photo_url_sync("bad-id", "https://example.com/r.jpg") is False


def test_pg_rituals_code_to_goal_display_maps():
    """CODE_TO_GOAL / CODE_TO_RESULT имеют ожидаемые display labels."""
    from arcana.repos.pg_rituals_repo import CODE_TO_GOAL, CODE_TO_RESULT, CODE_TO_PLACE
    assert CODE_TO_GOAL["protect"] == "🛡️ Защита"
    assert CODE_TO_GOAL["finance"] == "💰 Финансы"
    assert CODE_TO_RESULT["positive"] == "✅ Сработало"
    assert CODE_TO_RESULT["unverified"] == "⏳ Не проверено"
    assert CODE_TO_PLACE["home"] == "🏠 Дома"


# ── UX: невалидное имя клиента в ритуале → переспрос ────────────────────────

def _ritual_msg():
    bot_msg = MagicMock()
    bot_msg.chat.id = 100
    bot_msg.message_id = 555
    msg = MagicMock()
    msg.from_user.id = 7
    msg.answer = AsyncMock(return_value=bot_msg)
    return msg


_BASE_RITUAL_JSON = {
    "name": "Ритуал защиты",
    "goal": "protect",
    "needs_clarification": False,
    "consumables": "", "consumables_cost": 0,
    "duration_min": 60,
    "offerings": "", "forces": "", "structure": "",
    "amount": 0, "paid": 0, "payment_source": None,
    "offerings_cost": 0,
}


@pytest.mark.asyncio
async def test_invalid_client_name_ritual_sends_clarification():
    """LLM-рефузал в client_name → бот переспрашивает, клиент не создаётся."""
    import arcana.handlers.rituals as rit
    import core.client_resolve as cr

    bad_json = json.dumps(dict(_BASE_RITUAL_JSON, client_name="не могу извлечь имя"))
    msg = _ritual_msg()
    foc = AsyncMock()

    with patch("arcana.handlers.rituals.ask_claude", AsyncMock(return_value=bad_json)), \
         patch("arcana.handlers.rituals.get_user_tz", AsyncMock(return_value=3)), \
         patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=None)), \
         patch.object(cr, "find_or_create_client", foc):
        await rit.handle_add_ritual(msg, "ритуал", user_notion_id="u")

    texts = [c.args[0] for c in msg.answer.call_args_list if c.args]
    assert any("имя клиента" in t for t in texts)
    foc.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_client_name_ritual_no_clarification():
    """Валидное имя → переспрос не шлётся, resolve_or_create вызывается."""
    import arcana.handlers.rituals as rit
    import core.client_resolve as cr

    good_json = json.dumps(dict(_BASE_RITUAL_JSON, client_name="оля"))
    msg = _ritual_msg()
    roc = AsyncMock(return_value="c-olia")
    repo_result = MagicMock()
    repo_result.id = "ritual-1"

    with patch("arcana.handlers.rituals.ask_claude", AsyncMock(return_value=good_json)), \
         patch("arcana.handlers.rituals.get_user_tz", AsyncMock(return_value=3)), \
         patch("arcana.pending_tarot.get_pending", AsyncMock(return_value=None)), \
         patch.object(cr, "resolve_or_create", roc), \
         patch.object(rit._repo, "create", AsyncMock(return_value=repo_result)), \
         patch("core.message_pages.save_message_page", AsyncMock()):
        await rit.handle_add_ritual(msg, "ритуал оля", user_notion_id="u")

    roc.assert_awaited_once()
    texts = [c.args[0] for c in msg.answer.call_args_list if c.args]
    assert not any("имя клиента" in t for t in texts)
