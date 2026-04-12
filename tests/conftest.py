"""Общие фикстуры для тестов."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
import os
import sys

# Добавить корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Мок .env до импорта config — ПРИНУДИТЕЛЬНО перезаписываем чтобы не зависеть от .env файла
_TEST_ENV = {
    "NEXUS_BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz012345678",
    "ARCANA_BOT_TOKEN": "987654321:ABCdefGHIjklMNOpqrsTUVwxyz012345678",
    "ALLOWED_TELEGRAM_IDS": "67686090",
    "ANTHROPIC_API_KEY": "fake-key",
    "CLAUDE_HAIKU": "claude-haiku-4-5-20251001",
    "CLAUDE_SONNET": "claude-sonnet-4-20250514",
    "NOTION_TOKEN": "fake-token",
    "NOTION_DB_TASKS": "fake-db-id",
    "NOTION_DB_FINANCE": "fake-db-id",
    "NOTION_DB_MEMORY": "fake-db-id",
    "NOTION_DB_NOTES": "fake-db-id",
    "NOTION_DB_LISTS": "fake-db-id",
    "NOTION_DB_ERRORS": "fake-db-id",
    "NOTION_DB_USERS": "fake-db-id",
    "NOTION_DB_CLIENTS": "fake-db-id",
    "NOTION_DB_SESSIONS": "fake-db-id",
    "NOTION_DB_RITUALS": "fake-db-id",
    "NOTION_DB_WORKS": "fake-db-id",
    "NOTION_DB_GRIMOIRE": "fake-db-id",
    "OPENAI_API_KEY": "fake-key",
    "LOG_LEVEL": "WARNING",
    "LOG_FILE": "/dev/null",
}
for k, v in _TEST_ENV.items():
    if k not in os.environ or not os.environ[k]:
        os.environ[k] = v


# ── Исключить E2E тесты из pytest collection ──────────────────────────────────
collect_ignore = ["test_nexus.py", "test_arcana.py", "test_all.py",
                  "e2e_runner.py", "e2e_config.py"]


@pytest.fixture
def mock_message():
    """Создать мок aiogram Message."""
    def _make(text="", from_id=67686090, first_name="Кай", chat_id=67686090):
        msg = AsyncMock()
        msg.text = text
        msg.from_user = MagicMock()
        msg.from_user.id = from_id
        msg.from_user.first_name = first_name
        msg.from_user.language_code = "ru"
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat.type = "private"
        msg.message_id = 12345
        msg.date = datetime.now(timezone.utc)
        msg.answer = AsyncMock(return_value=MagicMock(message_id=12346))
        msg.reply = AsyncMock(return_value=MagicMock(message_id=12347))
        msg.bot = AsyncMock()
        msg.bot.set_message_reaction = AsyncMock()
        msg.bot.send_message = AsyncMock()
        msg.photo = None
        msg.voice = None
        msg.document = None
        msg.content_type = "text"
        return msg
    return _make


@pytest.fixture
def mock_callback():
    """Создать мок CallbackQuery."""
    def _make(data="", from_id=67686090, message_text=""):
        cb = AsyncMock()
        cb.data = data
        cb.from_user = MagicMock()
        cb.from_user.id = from_id
        cb.message = MagicMock()
        cb.message.text = message_text
        cb.message.message_id = 12345
        cb.message.chat = MagicMock()
        cb.message.chat.id = from_id
        cb.message.edit_text = AsyncMock()
        cb.message.edit_reply_markup = AsyncMock()
        cb.message.answer = AsyncMock()
        cb.message.bot = AsyncMock()
        cb.answer = AsyncMock()
        return cb
    return _make


@pytest.fixture
def mock_notion():
    """Мок всех Notion операций — патчим NotionClient синглтон."""
    mock_client = MagicMock()
    mock_client.create_page = AsyncMock(return_value="fake-page-id")
    mock_client.update_page = AsyncMock()
    mock_client.query_database = AsyncMock(return_value=[])
    mock_client._client = MagicMock()
    mock_client._client.pages.create = AsyncMock(return_value={
        "id": "fake-page-id",
        "url": "https://notion.so/fake-page"
    })
    mock_client._client.pages.update = AsyncMock(return_value={"id": "fake-page-id"})
    mock_client._client.databases.query = AsyncMock(return_value={"results": []})

    with patch("core.notion_client._notion", return_value=mock_client), \
         patch("core.notion_client._instance", mock_client):
        yield mock_client


@pytest.fixture
def mock_claude():
    """Мок Claude API ответов."""
    async def fake_classify(text, *args, **kwargs):
        """Предсказуемая классификация."""
        text_lower = text.lower()

        if any(w in text_lower for w in ["задача", "задач", "напомни"]):
            return {
                "type": "task",
                "title": text,
                "category": "💳 Прочее",
                "priority": "⚪ Можно потом",
                "deadline": None,
                "reminder": None,
                "repeat": "Нет"
            }
        elif any(w in text_lower for w in ["потратила", "расход", "₽", "р "]):
            return {
                "type": "expense",
                "amount": 100,
                "category": "🍜 Продукты",
                "description": "тест",
                "source": "💳 Карта"
            }
        elif any(w in text_lower for w in ["доход", "получила", "заработала"]):
            return {
                "type": "income",
                "amount": 1000,
                "category": "💰 Зарплата",
                "description": "тест"
            }
        elif any(w in text_lower for w in ["заметка", "заметк"]):
            return {"type": "note", "text": text, "tags": ["тест"]}
        elif any(w in text_lower for w in ["купить", "покупк"]):
            return {
                "type": "list_buy",
                "items": [{"name": "тест", "category": "🍜 Продукты"}]
            }
        else:
            return {"type": "unknown"}

    return fake_classify


@pytest.fixture
def user_notion_id():
    """Фейковый Notion user ID."""
    return "fake-user-notion-id"
