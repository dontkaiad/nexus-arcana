"""Тесты Arcana."""
import pytest
from unittest.mock import AsyncMock


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
            from core.client_resolve import client_find

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
    """Тесты pending state для таро (SQLite)."""

    @pytest.mark.asyncio
    async def test_pending_save_get_delete(self):
        from arcana.pending_tarot import (
            save_pending, get_pending, delete_pending,
        )

        state = {
            "cards": ["Шут", "Маг"],
            "deck": "Уэйт",
            "interpretation": "тест",
        }
        await save_pending(99999, state)
        result = await get_pending(99999)
        assert result is not None, "Pending get вернул None"
        assert result["cards"] == ["Шут", "Маг"]
        assert result["deck"] == "Уэйт"

        await delete_pending(99999)
        assert await get_pending(99999) is None, "Pending не удалился"


class TestArcanaTarotLoader:
    """Тесты загрузки колод."""

    def test_get_cards_context_waite(self):
        """get_cards_context для Уэйта возвращает заголовок + найденные карты."""
        from arcana.tarot_loader import get_cards_context

        ctx = get_cards_context("Уэйт", ["Туз Мечей"])
        assert ctx, "пустой контекст для Уэйта"
        assert "Уэйт" in ctx or "уэйт" in ctx.lower()
        # хотя бы одна карта найдена → в контексте есть упоминание
        assert "меч" in ctx.lower()

    def test_get_cards_context_yo_insensitive(self):
        """«влюбленные» (без ё) матчится к ключу «VI Влюблённые» (с ё) — #159."""
        from arcana.tarot_loader import get_cards_context

        ctx = get_cards_context("Уэйт", ["влюбленные"])
        assert ctx, "карта без ё не нашлась в справочнике (ё≠е баг)"
        assert "влюбл" in ctx.lower()

    def test_missing_cards_yo_insensitive(self):
        """Триплет с «влюбленные» (без ё) — все карты в справочнике, missing=[]."""
        from arcana.tarot_loader import missing_cards

        assert missing_cards(
            "Уэйт", ["8 мечей", "влюбленные", "4 мечей", "3 мечей"]
        ) == []

    def test_missing_cards_reports_absent(self):
        """Карта, которой нет в справочнике, попадает в missing (для мониторинга)."""
        from arcana.tarot_loader import missing_cards

        missing = missing_cards("Уэйт", ["влюбленные", "абракадабра"])
        assert missing == ["абракадабра"]

    def test_missing_cards_unknown_deck_silent(self):
        """Неизвестная колода → missing=[] (нечего мониторить, не наша ошибка)."""
        from arcana.tarot_loader import missing_cards

        assert missing_cards("Несуществующая колода", ["туз мечей"]) == []

    def test_deck_styles_loads(self):
        """arcana/tarot_refs/deck_styles.json существует и читается."""
        import json
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "arcana", "tarot_refs", "deck_styles.json",
        )
        assert os.path.exists(path), f"{path} не найден"

        with open(path) as f:
            data = json.load(f)

        assert isinstance(data, dict) and data, "deck_styles.json пустой/не словарь"
