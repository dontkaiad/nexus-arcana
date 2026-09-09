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


# ── Whitelist городов для tz (бывший test_user_tz_cities.py) ────────────────
# issue #70 follow-up: при «я в Алании» Claude Haiku возвращал UTC+2
# (устарел — Турция UTC+3 c 2016). Whitelist `_CITY_TZ` должен матчить
# турецкие/закавказские/израильские города ДО fallback на Claude.

@pytest.mark.parametrize("text,expected_tz", [
    ("я в Алании", 3),
    ("в Аланьи", 3),
    ("сейчас в Анталье", 3),
    ("я в Турции", 3),
    ("Стамбул", 3),
    ("Батуми", 4),
    ("я на Кипре, Ларнака", 2),
    ("Тель-Авив", 2),
])
@pytest.mark.asyncio
async def test_city_whitelist_resolves_tz_without_claude(text, expected_tz):
    """Whitelist срабатывает раньше Claude — ask_claude НЕ должен звониться."""
    from nexus.handlers import tasks as tasks_mod

    msg = MagicMock()
    msg.from_user.id = 999
    msg.answer = AsyncMock()

    ask = AsyncMock(side_effect=AssertionError("Claude не должен звониться для whitelist-города"))

    with patch.object(tasks_mod, "ask_claude", ask), \
         patch("core.repos.pg_memory_repo.PgMemoryRepo.upsert", AsyncMock()), \
         patch("core.location._invalidate_weather_cache"):
        await tasks_mod._update_user_tz(msg, text)

    assert tasks_mod._user_tz_offset[999] == expected_tz
    sign = "+" if expected_tz >= 0 else ""
    msg.answer.assert_awaited_once_with(f"🕐 Часовой пояс обновлён: UTC{sign}{expected_tz}")


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
        """Диалоговый pending «жду ответ/кнопку» не должен жить в module-level
        dict — теряется при рестарте (частый auto-reload на деплое = потерянный
        диалог). Используй core.pending_kv или dedicated pending_*.db (#206/#208)."""
        import os

        # Допустимы: не критичны к рестарту (потеря = мелкое неудобство).
        ALLOWED_PATTERNS = {
            "_pending_auto",      # авто-предложение запомнить (потеря = нет предложения)
            "_last_finance_ts",   # rate-limit timestamp
            "_photo_pending",     # ожидание фото (короткое окно)
            "_pending",           # work_reminder_kb / delete — TTL-swept, окно секунды
        }

        roots = ("nexus/handlers", "arcana/handlers")
        extra_files = ("nexus/nexus_bot.py", "arcana/bot.py")
        paths = list(extra_files)
        for r in roots:
            for root, _dirs, files in os.walk(r):
                paths += [os.path.join(root, f) for f in files if f.endswith(".py")]

        suspicious = []
        for path in paths:
            with open(path) as fh:
                content = fh.read()
            for i, line in enumerate(content.split("\n")):
                if "pending_" in line and "= {}" in line:
                    stripped = line.lstrip()
                    if len(line) - len(stripped) != 0:  # только module-level
                        continue
                    var_name = stripped.split(":")[0].split("=")[0].strip()
                    if var_name not in ALLOWED_PATTERNS:
                        suspicious.append(f"{path}:{i+1}: {stripped}")

        assert len(suspicious) == 0, \
            "Диалоговый pending в in-memory dict (переживёт ли рестарт?):\n" + "\n".join(suspicious)


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
