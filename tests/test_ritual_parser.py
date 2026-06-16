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
