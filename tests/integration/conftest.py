"""Конфигурация для интеграционных тестов."""
import logging
import os
import sys

import pytest
import sqlalchemy as sa

# Проект в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

logger = logging.getLogger("tests.integration.conftest")

# Тестовые переменные окружения — ПРИНУДИТЕЛЬНО перезаписываем
_TEST_ENV = {
    "NEXUS_BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789",
    "ARCANA_BOT_TOKEN": "987654321:ABCdefGHIjklMNOpqrsTUVwxyz987654321",
    "ALLOWED_TELEGRAM_IDS": "67686090",
    "ANTHROPIC_API_KEY": "sk-ant-test-fake-key",
    "CLAUDE_HAIKU": "claude-haiku-4-5-20251001",
    "CLAUDE_SONNET": "claude-sonnet-4-20250514",
    "NOTION_TOKEN": "ntn_test_fake_token",
    "NOTION_DB_TASKS": "00000000-0000-0000-0000-000000000001",
    "NOTION_DB_FINANCE": "00000000-0000-0000-0000-000000000002",
    "NOTION_DB_MEMORY": "00000000-0000-0000-0000-000000000003",
    "NOTION_DB_NOTES": "00000000-0000-0000-0000-000000000004",
    "NOTION_DB_LISTS": "00000000-0000-0000-0000-000000000005",
    "NOTION_DB_ERRORS": "00000000-0000-0000-0000-000000000006",
    "NOTION_DB_USERS": "00000000-0000-0000-0000-000000000007",
    "NOTION_DB_CLIENTS": "00000000-0000-0000-0000-000000000008",
    "NOTION_DB_SESSIONS": "00000000-0000-0000-0000-000000000009",
    "NOTION_DB_RITUALS": "00000000-0000-0000-0000-000000000010",
    "NOTION_DB_WORKS": "00000000-0000-0000-0000-000000000011",
    "NOTION_DB_GRIMOIRE": "00000000-0000-0000-0000-000000000012",
    "OPENAI_API_KEY": "sk-test-fake-key",
    "LOG_LEVEL": "WARNING",
    "LOG_FILE": "/dev/null",
}

for k, v in _TEST_ENV.items():
    if k not in os.environ or not os.environ[k]:
        os.environ[k] = v


# ── Очистка тестового пользователя из реального Postgres ────────────────────
# bot_factory.py прогоняет РЕАЛЬНЫЕ хендлеры через dp.feed_update() против
# РЕАЛЬНОГО core.db.get_engine() — только внешние API (Notion/Claude/OpenAI)
# замоканы (mock_externals.py). _fake_user_data() там подставляет фиксированный
# notion_page_id="test-user-notion-id" вместо реального пользователя. Раньше
# ничего не чистило за собой: строки копились в persistent Docker volume
# бесконечно (обнаружено при расследовании расхождения дев/прод в 🧠 Память —
# за ~2.5 месяца прогонов накопилось 398 мусорных memories, почти все с
# category="💡 Инсайт", т.к. мок ask_claude не отвечает валидным JSON парсера
# фактов и core/memory.py:_parse_fact падает в fallback-категорию).
TEST_USER_NOTION_ID = "test-user-notion-id"

# Все таблицы схемы с колонкой user_notion_id (не только memories, куда
# реально писали на момент находки) — на случай, если будущий тест начнёт
# писать тем же фейковым id в любую из них.
_CLEANUP_TABLES = (
    "memories", "tasks", "nexus_budget", "nexus_lists", "notes",
    "sessions", "works", "clients", "debts", "grimoire_entries",
    "arcana_inventory", "arcana_pnl",
)


def _delete_test_user_rows() -> None:
    try:
        from core.db import get_engine
        with get_engine().begin() as conn:
            for table in _CLEANUP_TABLES:
                conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE user_notion_id = :uid"),
                    {"uid": TEST_USER_NOTION_ID},
                )
    except Exception as e:
        # Best-effort: недоступная БД тут не должна маскировать реальные
        # сбои тестов своим собственным исключением — они и так упадут/
        # заскипятся сами (см. setup_nexus/setup_arcana try/except).
        logger.warning("integration cleanup: DELETE test-user rows failed: %s", e)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_user_rows():
    """До сессии — на случай, если предыдущий прогон прервался (Ctrl+C) и
    не дошёл до teardown. После сессии — чтобы этот прогон ничего не оставил
    в общем локальном Postgres (persistent volume, не per-test транзакция)."""
    _delete_test_user_rows()
    yield
    _delete_test_user_rows()
