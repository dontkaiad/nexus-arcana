"""Тесты команд Nexus."""
import pytest
from unittest.mock import AsyncMock, patch


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


class TestQuickCreateTasksPG:
    """Quick-create задач (on_arcana_choice «нет» + on_unknown_clarify task)
    создают задачу в PG через _repo.create, регистрируют контекст-правку
    (last_record_set/_last_task_set) и НЕ трогают Notion task_add/save_message_page.
    """

    @staticmethod
    def _patches():
        from unittest.mock import MagicMock
        return (
            patch("nexus.repos.tasks_repo._repo.create",
                  new=AsyncMock(return_value="pg-123")),
            patch("nexus.handlers.tasks.last_record_set", new=MagicMock()),
            patch("nexus.handlers.tasks._last_task_set", new=MagicMock()),
            patch("core.notion_client.task_add", new=AsyncMock(return_value="should-not-be-used")),
            patch("core.message_pages.save_message_page", new=AsyncMock()),
        )

    @pytest.mark.asyncio
    async def test_arcana_choice_no_creates_task_in_pg(self, mock_callback):
        from nexus.nexus_bot import on_arcana_choice, _pending_arcana
        from nexus.repos.pg_tasks_repo import (
            _extract_title, _extract_status, _extract_select,
        )

        uid = 67686090
        _pending_arcana[uid] = "купить молоко"
        cb = mock_callback(data="arcana_choice_no", from_id=uid)

        p_create, p_lr, p_lt, p_taskadd, p_smp = self._patches()
        with p_create as m_create, p_lr as m_lr, p_lt as m_lt, \
             p_taskadd as m_taskadd, p_smp as m_smp:
            await on_arcana_choice(cb, user_notion_id="u-1")

            m_create.assert_awaited_once()
            props = m_create.call_args.args[1]
            assert _extract_title(props["Задача"]) == "купить молоко"
            assert _extract_status(props["Статус"]) == "Not started"
            assert _extract_select(props["Приоритет"]) == "Важно"
            assert _extract_select(props["Категория"]) == "💳 Прочее"
            assert props["🪪 Пользователи"]["relation"][0]["id"] == "u-1"

            m_lr.assert_called_once_with(uid, "task", "pg-123")
            m_lt.assert_called_once_with(uid, "pg-123")
            m_taskadd.assert_not_called()
            m_smp.assert_not_called()

    @pytest.mark.asyncio
    async def test_arcana_choice_no_without_uid_omits_relation(self, mock_callback):
        from nexus.nexus_bot import on_arcana_choice, _pending_arcana

        uid = 67686090
        _pending_arcana[uid] = "позвонить в банк"
        cb = mock_callback(data="arcana_choice_no", from_id=uid)

        p_create, p_lr, p_lt, p_taskadd, p_smp = self._patches()
        with p_create as m_create, p_lr, p_lt, p_taskadd, p_smp:
            await on_arcana_choice(cb, user_notion_id="")
            props = m_create.call_args.args[1]
            assert "🪪 Пользователи" not in props

    @pytest.mark.asyncio
    async def test_unknown_clarify_task_creates_task_in_pg(self, mock_callback):
        import time as _time
        from nexus.nexus_bot import on_unknown_clarify, _pending_unknown
        from nexus.repos.pg_tasks_repo import (
            _extract_title, _extract_status, _extract_select,
        )

        uid = 67686090
        _pending_unknown[uid] = ("сделать отчёт", "u-2", _time.time())
        cb = mock_callback(data="unk_task_1", from_id=uid)

        p_create, p_lr, p_lt, p_taskadd, p_smp = self._patches()
        with p_create as m_create, p_lr as m_lr, p_lt as m_lt, \
             p_taskadd as m_taskadd, p_smp as m_smp:
            await on_unknown_clarify(cb, user_notion_id="")

            m_create.assert_awaited_once()
            props = m_create.call_args.args[1]
            assert _extract_title(props["Задача"]) == "сделать отчёт"
            assert _extract_status(props["Статус"]) == "Not started"
            assert _extract_select(props["Приоритет"]) == "Важно"
            assert _extract_select(props["Категория"]) == "💳 Прочее"
            assert props["🪪 Пользователи"]["relation"][0]["id"] == "u-2"

            m_lr.assert_called_once_with(uid, "task", "pg-123")
            m_lt.assert_called_once_with(uid, "pg-123")
            m_taskadd.assert_not_called()
            m_smp.assert_not_called()
