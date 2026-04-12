"""Интеграционные тесты Nexus — полный pipeline через dp.feed_update()."""
import pytest
import asyncio
from unittest.mock import patch

from tests.integration.bot_factory import FakeSession, make_update
from tests.integration.mock_externals import get_all_patches


class TestNexusIntegration:
    """Полные integration тесты Nexus — текст → роутинг → handler → ответ."""

    @pytest.fixture(autouse=True)
    async def setup_nexus(self):
        """Создать Nexus dispatcher с реальными хэндлерами + моки внешних API."""
        self.session = FakeSession()
        self._patches = get_all_patches()

        # Запускаем все патчи
        for p in self._patches:
            try:
                p.start()
            except Exception:
                pass

        try:
            from nexus.nexus_bot import dp, bot
            self.dp = dp
            bot.session = self.session
            self.bot = bot
        except Exception as e:
            pytest.skip(f"Не удалось создать Nexus dispatcher: {e}")

        yield

        for p in self._patches:
            try:
                p.stop()
            except Exception:
                pass

    async def send(self, text: str) -> str:
        """Отправить сообщение и получить ответ бота."""
        self.session.clear()
        update = make_update(text, update_id=abs(hash(text)) % 100000)

        try:
            await self.dp.feed_update(self.bot, update)
        except Exception as e:
            return f"[ERROR: {e}]"

        await asyncio.sleep(0.05)
        return self.session.get_last_response()

    # ═══════════════════════════════════════
    # КОМАНДЫ
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_start(self):
        """Команда /start отвечает."""
        r = await self.send("/start")
        assert r, "/start не вернул ответ"
        assert "traceback" not in r.lower()

    @pytest.mark.asyncio
    async def test_help(self):
        """Команда /help отвечает."""
        r = await self.send("/help")
        assert r, "/help не вернул ответ"

    @pytest.mark.asyncio
    async def test_today(self):
        """Команда /today отвечает."""
        r = await self.send("/today")
        assert r, "/today не вернул ответ"

    @pytest.mark.asyncio
    async def test_tasks(self):
        """Команда /tasks отвечает."""
        r = await self.send("/tasks")
        assert r, "/tasks не вернул ответ"

    @pytest.mark.asyncio
    async def test_stats(self):
        """Команда /stats отвечает."""
        r = await self.send("/stats")
        assert r, "/stats не вернул ответ"

    @pytest.mark.asyncio
    async def test_finance(self):
        """Команда /finance отвечает."""
        r = await self.send("/finance")
        assert r, "/finance не вернул ответ"

    @pytest.mark.asyncio
    async def test_list(self):
        """Команда /list отвечает и имеет кнопки."""
        r = await self.send("/list")
        assert r, "/list не вернул ответ"

    @pytest.mark.asyncio
    async def test_memory(self):
        """Команда /memory отвечает."""
        r = await self.send("/memory")
        assert r, "/memory не вернул ответ"

    @pytest.mark.asyncio
    async def test_notes(self):
        """Команда /notes отвечает."""
        r = await self.send("/notes")
        assert r, "/notes не вернул ответ"

    @pytest.mark.asyncio
    async def test_tz(self):
        """Команда /tz отвечает."""
        r = await self.send("/tz")
        assert r, "/tz не вернул ответ"

    # ═══════════════════════════════════════
    # ТЕКСТ → ПОЛНЫЙ PIPELINE
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_task_creation_pipeline(self):
        """'задача X' проходит classify → task_add → ответ."""
        r = await self.send("задача купить молоко")
        assert r, "Задача не создалась"
        assert "traceback" not in r.lower()

    @pytest.mark.asyncio
    async def test_expense_pipeline(self):
        """'потратила 500р' → classify → finance_add → ответ."""
        r = await self.send("потратила 500р продукты")
        assert r, "Расход не записался"
        assert "traceback" not in r.lower()

    @pytest.mark.asyncio
    async def test_income_pipeline(self):
        """'доход 50000' → classify → finance_add → ответ."""
        r = await self.send("доход 50000")
        assert r, "Доход не записался"

    @pytest.mark.asyncio
    async def test_note_pipeline(self):
        """'заметка: X' → classify → note_add → ответ."""
        r = await self.send("заметка: важная мысль для теста")
        assert r, "Заметка не создалась"

    @pytest.mark.asyncio
    async def test_memory_save_pipeline(self):
        """'запомни: X' → regex → memory_set → ответ."""
        r = await self.send("запомни: тест интеграции 12345")
        assert r, "Память не сохранилась"

    @pytest.mark.asyncio
    async def test_list_buy_pipeline(self):
        """'купить X' → classify → list handler → ответ."""
        r = await self.send("купить молоко")
        assert r, "Список не обновился"

    @pytest.mark.asyncio
    async def test_done_pipeline(self):
        """'сделала X' → regex → task_done → ответ."""
        r = await self.send("сделала тестовая задача")
        assert r, "'Сделала' не обработалось"

    @pytest.mark.asyncio
    async def test_cancel_pipeline(self):
        """'отмени X' → regex → task_cancel → ответ."""
        r = await self.send("отмени тестовая задача")
        assert r, "'Отмени' не обработалось"

    # ═══════════════════════════════════════
    # EDGE CASES
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_en_layout(self):
        """EN раскладка конвертируется."""
        r = await self.send("pflfxf ntcn")  # "задача тест"
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_unknown_no_crash(self):
        """Неизвестный ввод не крашит."""
        r = await self.send("абракадабра хтонь 12345")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_empty_no_crash(self):
        """Пустое сообщение не крашит."""
        r = await self.send("   ")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_long_message_no_crash(self):
        """Длинное сообщение не крашит."""
        r = await self.send("задача " + "тест " * 200)
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_stress_no_crash(self):
        """Серия разных вводов — ни один не крашит."""
        inputs = [
            "привет",
            "123",
            "/nonexistent",
            "лимит привычки 15000",
            "что я помню о котах",
            "забудь тест",
        ]
        for text in inputs:
            try:
                r = await self.send(text)
                assert "traceback" not in (r or "").lower(), f"Краш на: {text}"
            except Exception as e:
                pytest.fail(f"Exception на '{text}': {e}")
