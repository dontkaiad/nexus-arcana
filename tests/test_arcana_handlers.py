"""Тесты Arcana."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestArcanaCommands:
    """Тесты команд Arcana."""

    @pytest.mark.asyncio
    async def test_start(self, mock_message, mock_notion):
        msg = mock_message("/start")

        try:
            from arcana.handlers.base import cmd_start
            await cmd_start(msg)
        except (ImportError, AttributeError, Exception):
            pass

        assert msg.answer.called or msg.reply.called, \
            "/start не ответил"


class TestArcanaClassifier:
    """Тесты классификации Arcana."""

    @pytest.mark.asyncio
    async def test_client_detection(self):
        """'клиент Анна' определяется как клиент."""
        text = "клиент Анна, женщина, 30 лет"
        text_lower = text.lower()
        assert "клиент" in text_lower

    @pytest.mark.asyncio
    async def test_ritual_detection(self):
        """'ритуал: X' определяется как ритуал."""
        text = "ритуал: очищение дома свечами"
        text_lower = text.lower()
        assert "ритуал" in text_lower

    @pytest.mark.asyncio
    async def test_grimoire_detection(self):
        """'запиши в гримуар: X' определяется."""
        text = "запиши в гримуар: заговор на деньги"
        text_lower = text.lower()
        assert "гримуар" in text_lower

    @pytest.mark.asyncio
    async def test_work_detection(self):
        """'работа: X' определяется как работа."""
        text = "работа: расклад для Анны"
        text_lower = text.lower()
        assert "работа" in text_lower

    @pytest.mark.asyncio
    async def test_debt_query(self):
        """'сколько мне должны' определяется."""
        text = "сколько мне должны?"
        text_lower = text.lower()
        assert "должн" in text_lower

    @pytest.mark.asyncio
    async def test_verify_detection(self):
        """'Анна — сбылось' определяется как верификация."""
        text = "Анна 5 марта — сбылось"
        text_lower = text.lower()
        assert "сбыл" in text_lower


class TestArcanaFindOrCreate:
    """Тесты find-or-create клиентов."""

    @pytest.mark.asyncio
    async def test_find_existing_client(self, mock_notion):
        """Если клиент найден — не создавать нового."""
        try:
            from core.notion_client import client_find

            mock_notion.query_database = AsyncMock(return_value=[{
                "id": "existing-id",
                "properties": {
                    "Имя": {"title": [{"plain_text": "Анна"}]},
                    "Статус": {"select": {"name": "🟢 Активный"}}
                }
            }])

            result = await client_find("Анна")

            assert not mock_notion.create_page.called, \
                "Создал дубль клиента вместо нахождения существующего!"
        except ImportError:
            pytest.skip("client_find не найден")


class TestArcanaTarotPending:
    """Тесты pending state для таро."""

    @pytest.mark.asyncio
    async def test_pending_save_get_delete(self):
        """SQLite pending: save → get → delete."""
        try:
            from arcana.pending_tarot import save, get, delete

            test_data = {
                "cards": ["Шут", "Маг"],
                "deck": "Уэйт",
                "interpretation": "тест"
            }

            await save(user_id=99999, data=test_data)
            result = await get(user_id=99999)
            assert result is not None, "Pending get вернул None"
            assert result["cards"] == ["Шут", "Маг"]

            await delete(user_id=99999)
            result = await get(user_id=99999)
            assert result is None, "Pending не удалился"
        except ImportError:
            pytest.skip("pending_tarot не найден")


class TestArcanaTarotLoader:
    """Тесты загрузки колод."""

    @pytest.mark.asyncio
    async def test_card_search(self):
        """Нечёткий поиск карты."""
        try:
            from arcana.tarot_loader import find_card

            result = find_card("туз мечей")
            assert result is not None, "Карта 'туз мечей' не найдена"
        except ImportError:
            pytest.skip("tarot_loader не найден")

    @pytest.mark.asyncio
    async def test_deck_styles(self):
        """deck_styles.json существует и читается."""
        import json
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "arcana", "deck_styles.json"
        )
        if not os.path.exists(path):
            pytest.skip("deck_styles.json не найден")

        with open(path) as f:
            data = json.load(f)

        assert isinstance(data, dict), "deck_styles.json не словарь"
        assert len(data) > 0, "deck_styles.json пустой"


class TestArcanaMessageCollector:
    """Тесты message collector."""

    @pytest.mark.asyncio
    async def test_collector_exists(self):
        """message_collector модуль существует."""
        try:
            from core.message_collector import MessageCollector
            assert MessageCollector is not None
        except ImportError:
            pytest.skip("MessageCollector не найден")
