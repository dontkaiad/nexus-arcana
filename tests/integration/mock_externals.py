"""Моки для Notion, Claude, OpenAI — все внешние зависимости."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import json


def _fake_user_data(tg_id: int = 67686090) -> dict:
    """Данные пользователя как из get_user()."""
    return {
        "notion_page_id": "test-user-notion-id",
        "name": "Кай",
        "role": "👑 Владелец",
        "permissions": {
            "nexus": True,
            "arcana": True,
            "finance": True,
        },
        "_ts": 9999999999,
    }


async def _fake_get_user(tg_id: int) -> dict:
    """Мок get_user — всегда находит пользователя."""
    return _fake_user_data(tg_id)


async def _fake_ask_claude(text_or_prompt, system=None, max_tokens=1024,
                           model=None, **kwargs):
    """Мок ask_claude — возвращает предсказуемый JSON-роутинг."""
    # Если это вызов из classify — нужно вернуть JSON с типом
    text = text_or_prompt.lower().strip() if isinstance(text_or_prompt, str) else ""

    # Задачи
    if any(w in text for w in ["задача ", "задач "]):
        return json.dumps([{
            "type": "task",
            "title": text.replace("задача ", "").strip(),
            "category": "💳 Прочее",
            "priority": "⚪ Можно потом",
            "deadline": None,
            "reminder": None,
            "repeat": "Нет"
        }])

    # Расходы
    if any(w in text for w in ["потратила", "расход"]):
        import re
        m = re.search(r"(\d+)", text)
        amt = int(m.group(1)) if m else 100
        return json.dumps([{
            "type": "expense",
            "amount": amt,
            "category": "🍜 Продукты",
            "description": text,
            "source": "💳 Карта",
        }])

    # Доходы
    if any(w in text for w in ["доход", "получила"]):
        return json.dumps([{
            "type": "income",
            "amount": 1000,
            "category": "💰 Зарплата",
            "description": text,
        }])

    # Заметки
    if "заметка" in text:
        return json.dumps([{
            "type": "note",
            "text": text,
            "tags": ["тест"],
        }])

    # Списки
    if any(w in text for w in ["купить", "купи "]):
        return json.dumps([{
            "type": "list_buy",
            "items": [{"name": text.replace("купить ", ""), "qty": "1"}],
        }])

    # Arcana-specific
    if "клиент" in text:
        return "new_client"
    if "ритуал" in text:
        return "ritual"
    if "работа" in text:
        return "work"
    if "гримуар" in text:
        return "grimoire_add"
    if "сколько" in text and "должн" in text:
        return "debt"
    if "сбыл" in text:
        return "verify"

    # Unknown
    return json.dumps([{"type": "unknown"}])


async def _fake_query_pages(db_id, filters=None, page_size=20, sorts=None):
    """Мок query_pages — пустой результат по умолчанию."""
    return []


async def _fake_page_create(db_id, props):
    """Мок page_create — возвращает fake page id."""
    return "fake-page-id-001"


async def _fake_tasks_active(user_notion_id="", include_in_progress=True):
    """Мок tasks_active — пустой список задач."""
    return []


async def _fake_finance_month(month, user_notion_id="", **kwargs):
    """Мок finance_month — пустой список."""
    return []


async def _fake_get_user_tz(tg_id):
    """Мок get_user_tz — UTC+3."""
    return 3


async def _fake_memory_get(key):
    """Мок memory_get — ничего не найдено."""
    return None


async def _fake_memory_set(key, value, category="", user_notion_id=""):
    """Мок memory_set — ничего не делать."""
    return None


async def _fake_log_error(*args, **kwargs):
    """Мок log_error — ничего не делать."""
    pass


async def _fake_match_select(db_id, prop_name, value):
    """Мок match_select — возвращает value с emoji prefix."""
    return f"💳 {value}"


async def _fake_note_add(*args, **kwargs):
    """Мок note_add."""
    return "fake-note-id"


async def _fake_task_add(*args, **kwargs):
    """Мок task_add."""
    return "fake-task-id"


async def _fake_finance_add(*args, **kwargs):
    """Мок finance_add."""
    return "fake-finance-id"


async def _fake_notes_search(query, user_notion_id=""):
    """Мок notes_search — пусто."""
    return []


async def _fake_client_find(name, user_notion_id=""):
    """Мок client_find — не найден."""
    return None


async def _fake_client_add(*args, **kwargs):
    """Мок client_add."""
    return "fake-client-id"


async def _fake_grimoire_add(*args, **kwargs):
    """Мок grimoire_add."""
    return "fake-grimoire-id"


async def _fake_work_add(*args, **kwargs):
    """Мок work_add."""
    return "fake-work-id"


async def _fake_ritual_add(*args, **kwargs):
    """Мок ritual_add."""
    return "fake-ritual-id"


async def _fake_session_add(*args, **kwargs):
    """Мок session_add."""
    return "fake-session-id"


async def _fake_works_list(user_notion_id=""):
    """Мок works_list — пусто."""
    return []


async def _fake_sessions_all(user_notion_id="", sbylos_filter=None):
    """Мок sessions_all — пусто."""
    return []


async def _fake_rituals_all(user_notion_id="", result_filter=None):
    """Мок rituals_all — пусто."""
    return []


async def _fake_arcana_all_debts(user_notion_id=""):
    """Мок arcana_all_debts — пусто."""
    return []


async def _fake_arcana_finance_summary(*args, **kwargs):
    """Мок arcana_finance_summary — пустая сводка."""
    return {"income": 0, "expense": 0, "profit": 0, "records": []}


async def _fake_grimoire_list_by_category(*args, **kwargs):
    """Мок grimoire_list_by_category — пусто."""
    return []


async def _fake_grimoire_search(*args, **kwargs):
    """Мок grimoire_search — пусто."""
    return []


async def _fake_get_db_options(db_id, prop_name):
    """Мок get_db_options — стандартные категории."""
    return ["🍜 Продукты", "🚬 Привычки", "💳 Прочее"]


def get_all_patches() -> list:
    """Вернуть список всех patch объектов для внешних зависимостей."""
    patches = [
        # User manager — критично для middleware
        patch("core.user_manager.get_user", side_effect=_fake_get_user),

        # Claude API
        patch("core.claude_client.ask_claude", side_effect=_fake_ask_claude),
        patch("core.claude_client.ask_claude_vision", new_callable=AsyncMock,
              return_value="Тестовый ответ vision"),

        # Notion — высокоуровневые функции
        patch("core.notion_client.page_create", side_effect=_fake_page_create),
        patch("core.notion_client.query_pages", side_effect=_fake_query_pages),
        patch("core.notion_client.update_page", new_callable=AsyncMock),
        patch("core.notion_client.log_error", side_effect=_fake_log_error),
        patch("core.notion_client.match_select", side_effect=_fake_match_select),
        patch("core.notion_client.get_db_options", side_effect=_fake_get_db_options),
        patch("core.notion_client.task_add", side_effect=_fake_task_add),
        patch("core.notion_client.tasks_active", side_effect=_fake_tasks_active),
        patch("core.notion_client.finance_add", side_effect=_fake_finance_add),
        patch("core.notion_client.finance_month", side_effect=_fake_finance_month),
        patch("core.notion_client.note_add", side_effect=_fake_note_add),
        patch("core.notion_client.notes_search", side_effect=_fake_notes_search),
        patch("core.notion_client.memory_get", side_effect=_fake_memory_get),
        patch("core.notion_client.memory_set", side_effect=_fake_memory_set),
        patch("core.notion_client.client_find", side_effect=_fake_client_find),
        patch("core.notion_client.client_add", side_effect=_fake_client_add),
        patch("core.notion_client.session_add", side_effect=_fake_session_add),
        patch("core.notion_client.ritual_add", side_effect=_fake_ritual_add),
        patch("core.notion_client.work_add", side_effect=_fake_work_add),
        patch("core.notion_client.works_list", side_effect=_fake_works_list),
        patch("core.notion_client.sessions_all", side_effect=_fake_sessions_all),
        patch("core.notion_client.rituals_all", side_effect=_fake_rituals_all),
        patch("core.notion_client.arcana_all_debts", side_effect=_fake_arcana_all_debts),
        patch("core.notion_client.arcana_finance_summary", side_effect=_fake_arcana_finance_summary),
        patch("core.notion_client.grimoire_add", side_effect=_fake_grimoire_add),
        patch("core.notion_client.grimoire_list_by_category", side_effect=_fake_grimoire_list_by_category),
        patch("core.notion_client.grimoire_search", side_effect=_fake_grimoire_search),

        # Timezone
        patch("core.shared_handlers.get_user_tz", side_effect=_fake_get_user_tz),
    ]
    return patches
