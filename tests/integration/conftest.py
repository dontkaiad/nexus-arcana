"""Конфигурация для интеграционных тестов."""
import os
import sys

# Проект в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

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
