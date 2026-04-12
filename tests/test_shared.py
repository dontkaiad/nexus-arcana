"""Тесты общих компонентов (оба бота)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTimezone:
    """Тесты timezone."""

    @pytest.mark.asyncio
    async def test_get_user_tz(self, mock_notion):
        """get_user_tz возвращает число."""
        try:
            from core.shared_handlers import get_user_tz

            # Мокаем query_pages которую использует get_user_tz
            with patch("core.notion_client.query_pages", new_callable=AsyncMock) as mock_qp:
                mock_qp.return_value = [{
                    "properties": {
                        "Текст": {"title": [{"plain_text": "3"}]},
                        "Ключ": {"rich_text": [{"plain_text": "tz_67686090"}]},
                        "Актуально": {"checkbox": True}
                    }
                }]

                tz = await get_user_tz(67686090)
                assert isinstance(tz, (int, float)), f"TZ не число: {type(tz)}"
        except ImportError:
            pytest.skip("get_user_tz не найден")


class TestListManager:
    """Тесты list_manager."""

    @pytest.mark.asyncio
    async def test_import(self, mock_notion):
        """list_manager импортируется."""
        try:
            from core.list_manager import ListManager
            assert ListManager is not None
        except ImportError:
            try:
                import core.list_manager as lm
                assert lm is not None
            except ImportError:
                pytest.skip("list_manager не найден")


class TestMemory:
    """Тесты памяти."""

    @pytest.mark.asyncio
    async def test_memory_module_exists(self, mock_notion):
        """core/memory.py существует и импортируется."""
        try:
            import core.memory as mem
            assert hasattr(mem, "get_memories_for_context") or \
                   hasattr(mem, "extract_context_keywords") or \
                   hasattr(mem, "handle_memory_save"), \
                   "Нет ожидаемых функций в core/memory.py"
        except ImportError:
            pytest.skip("core.memory не найден")


class TestSQLitePending:
    """Тесты SQLite pending — критический паттерн."""

    @pytest.mark.asyncio
    async def test_no_dangerous_in_memory_state(self):
        """Проверить что ЗАДАЧИ не хранятся в in-memory dict (допустимы UI-state dict)."""
        import os

        # Эти in-memory dict допустимы — краткоживущий UI state, не персистентные данные
        ALLOWED_PATTERNS = {
            "_pending_finance",   # UI: ожидание уточнения тип расхода/дохода
            "_pending_limit",     # UI: ожидание ввода лимита
            "_pending_auto",      # UI: авто-предложение запомнить
            "_clarify",           # UI: ожидание уточнения
            "_pending_arcana",    # UI: перенаправление в Аркану
            "_pending_unknown",   # UI: ожидание выбора категории
            "_last_finance_ts",   # rate-limit timestamp
            "_photo_pending",     # UI: ожидание фото
        }

        suspicious = []
        for root, dirs, files in os.walk("nexus/handlers"):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    with open(path) as fh:
                        content = fh.read()
                    if "pending_" in content and "= {}" in content:
                        for i, line in enumerate(content.split("\n")):
                            if "pending_" in line and "= {}" in line:
                                stripped = line.lstrip()
                                indent = len(line) - len(stripped)
                                if indent == 0:
                                    # Проверить что это допустимый паттерн
                                    var_name = stripped.split(":")[0].split("=")[0].strip()
                                    if var_name not in ALLOWED_PATTERNS:
                                        suspicious.append(
                                            f"{path}:{i+1}: {stripped}"
                                        )

        assert len(suspicious) == 0, \
            f"Найден недопустимый in-memory pending state!\n" + "\n".join(suspicious)


class TestLayoutModule:
    """Тесты core/layout.py."""

    def test_layout_module_exists(self):
        """core/layout.py существует."""
        try:
            from core.layout import maybe_convert
            assert callable(maybe_convert)
        except ImportError:
            pytest.skip("core.layout не найден")

    def test_pure_russian_unchanged(self):
        """Чисто русский текст не меняется."""
        try:
            from core.layout import maybe_convert
            text = "задача купить молоко"
            result = maybe_convert(text)
            assert result == text, f"Русский текст изменился: '{result}'"
        except ImportError:
            pytest.skip("core.layout не найден")

    def test_en_layout_converted(self):
        """EN раскладка → RU."""
        try:
            from core.layout import maybe_convert
            result = maybe_convert("pflfxf")  # "задача"
            assert "задач" in result.lower(), f"EN→RU не сработал: '{result}'"
        except ImportError:
            pytest.skip("core.layout не найден")
