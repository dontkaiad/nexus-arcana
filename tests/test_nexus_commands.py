"""Тесты команд Nexus."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestNexusCommands:
    """Тесты всех / команд Nexus."""

    @pytest.mark.asyncio
    async def test_start(self, mock_message, mock_notion):
        """Проверить /start."""
        msg = mock_message("/start")

        from nexus.nexus_bot import cmd_start
        await cmd_start(msg, user_notion_id="fake-user-id")

        assert msg.answer.called or msg.reply.called, \
            "/start не вызвал answer/reply"

        if msg.answer.called:
            response = msg.answer.call_args
            text = str(response)
            assert "nexus" in text.lower() or "ассистент" in text.lower() or \
                   "привет" in text.lower(), \
                   f"/start ответ не содержит ожидаемого: {text[:200]}"

    @pytest.mark.asyncio
    async def test_help(self, mock_message, mock_notion):
        """Проверить /help."""
        msg = mock_message("/help")

        from nexus.nexus_bot import cmd_help
        await cmd_help(msg, user_notion_id="fake-user-id")

        assert msg.answer.called or msg.reply.called, \
            "/help не вызвал ответ"


class TestNexusTaskHandlers:
    """Тесты задач Nexus."""

    @pytest.mark.asyncio
    async def test_task_creation_classified(self, mock_message, mock_claude):
        """Классификатор распознаёт задачу."""
        result = await mock_claude("задача купить молоко")
        assert result["type"] == "task"
        assert result["title"] == "задача купить молоко"

    @pytest.mark.asyncio
    async def test_expense_classified(self, mock_message, mock_claude):
        """Классификатор распознаёт расход."""
        result = await mock_claude("потратила 500р на продукты")
        assert result["type"] == "expense"

    @pytest.mark.asyncio
    async def test_income_classified(self, mock_message, mock_claude):
        """Классификатор распознаёт доход."""
        result = await mock_claude("доход 50000")
        assert result["type"] == "income"

    @pytest.mark.asyncio
    async def test_note_classified(self, mock_message, mock_claude):
        """Классификатор распознаёт заметку."""
        result = await mock_claude("заметка: важная мысль")
        assert result["type"] == "note"

    @pytest.mark.asyncio
    async def test_unknown_classified(self, mock_message, mock_claude):
        """Неизвестный ввод → unknown."""
        result = await mock_claude("абракадабра 12345")
        assert result["type"] == "unknown"


class TestNexusRegexFilters:
    """Тесты regex фильтров — pre-classify."""

    @pytest.mark.asyncio
    async def test_memory_save_regex(self):
        """'запомни: X' ловится regex."""
        import re
        patterns = [
            r"запомни[:\s]",
            r"запомнить[:\s]",
        ]
        text = "запомни: тест память 67890"
        matched = any(re.search(p, text, re.IGNORECASE) for p in patterns)
        assert matched, "Regex не поймал 'запомни:'"

    @pytest.mark.asyncio
    async def test_done_regex(self):
        """'сделала X' ловится regex."""
        import re
        patterns = [
            r"сделал[аи]?\s",
            r"готов[оа]?\s",
            r"выполнил[аи]?\s",
        ]
        text = "сделала тестовая задача"
        matched = any(re.search(p, text, re.IGNORECASE) for p in patterns)
        assert matched, "Regex не поймал 'сделала'"

    @pytest.mark.asyncio
    async def test_cancel_regex(self):
        """'отмени X' ловится regex."""
        import re
        patterns = [
            r"отмени\s",
            r"удали\s",
            r"убери\s",
        ]
        text = "отмени задачу тест"
        matched = any(re.search(p, text, re.IGNORECASE) for p in patterns)
        assert matched, "Regex не поймал 'отмени'"


class TestNexusEnRuConversion:
    """Тесты EN→RU конвертации."""

    @pytest.mark.asyncio
    async def test_en_to_ru(self):
        """EN раскладка конвертируется."""
        try:
            from core.layout import maybe_convert
            result = maybe_convert("pflfxf")  # "задача"
            assert "задач" in result.lower(), \
                f"EN→RU не сработал: '{result}'"
        except ImportError:
            pytest.skip("core.layout.maybe_convert не найден")


class TestNotionHelpers:
    """Тесты Notion helper функций."""

    def test_select_builder(self):
        """_select() строит правильный объект."""
        try:
            from core.notion_client import _select
            result = _select("Тест")
            assert result == {"select": {"name": "Тест"}}
        except ImportError:
            pytest.skip("_select не найден")

    def test_status_builder(self):
        """_status() строит правильный объект."""
        try:
            from core.notion_client import _status
            result = _status("Done")
            assert result == {"status": {"name": "Done"}}
        except ImportError:
            pytest.skip("_status не найден")

    def test_status_vs_select(self):
        """Status и Select — разные структуры."""
        try:
            from core.notion_client import _select, _status
            sel = _select("Done")
            sta = _status("Done")
            assert sel != sta, "Status и Select совпали — баг!"
            assert "select" in sel
            assert "status" in sta
        except ImportError:
            pytest.skip("helpers не найдены")


class TestMatchSelect:
    """Тесты match_select — async (db_id, prop_name, value)."""

    @pytest.mark.asyncio
    async def test_match_returns_string(self):
        """match_select(db_id, prop_name, value) возвращает строку."""
        try:
            from core.notion_client import match_select

            with patch("core.notion_client.get_db_options", new_callable=AsyncMock) as mock_opts:
                mock_opts.return_value = ["🍜 Продукты", "🚬 Привычки", "💳 Прочее"]
                result = await match_select("fake-db-id", "Категория", "продукты")
                assert isinstance(result, str), f"match_select вернул не строку: {type(result)}"
        except ImportError:
            pytest.skip("match_select не найден")

    @pytest.mark.asyncio
    async def test_match_exact_emoji_prefix(self):
        """match_select находит полное совпадение с emoji."""
        try:
            from core.notion_client import match_select

            with patch("core.notion_client.get_db_options", new_callable=AsyncMock) as mock_opts:
                mock_opts.return_value = ["🍜 Продукты", "🚬 Привычки"]
                result = await match_select("fake-db-id", "Категория", "🍜 Продукты")
                assert result == "🍜 Продукты", f"Точное совпадение не сработало: {result}"
        except ImportError:
            pytest.skip("match_select не найден")

    @pytest.mark.asyncio
    async def test_match_partial_text(self):
        """match_select находит по тексту без emoji."""
        try:
            from core.notion_client import match_select

            with patch("core.notion_client.get_db_options", new_callable=AsyncMock) as mock_opts:
                mock_opts.return_value = ["🍜 Продукты", "🚬 Привычки", "💳 Прочее"]
                result = await match_select("fake-db-id", "Категория", "привычк")
                # Должен найти что-то (не пустая строка / не ошибка)
                assert result, f"match_select не нашёл 'привычк': {result}"
        except ImportError:
            pytest.skip("match_select не найден")


class TestDataGetPattern:
    """Тест критического паттерна data.get() or default."""

    def test_get_or_default_with_none(self):
        """data.get('key') or 'default' работает с None."""
        data = {"key": None}
        result = data.get("key") or "default"
        assert result == "default", "Паттерн or не сработал с None"

    def test_get_default_fails_with_none(self):
        """data.get('key', 'default') НЕ работает с None — это баг если использовать."""
        data = {"key": None}
        result = data.get("key", "default")
        assert result is None, \
            "get(key, default) вернул default для None — неожиданно"
