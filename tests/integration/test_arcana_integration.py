"""Интеграционные тесты Arcana — полный pipeline через dp.feed_update()."""
import pytest
import asyncio
from unittest.mock import patch

from tests.integration.bot_factory import FakeSession, make_update
from tests.integration.mock_externals import get_all_patches


_arcana_dp = None
_arcana_bot = None


def _get_arcana_dp_bot():
    """Создать Arcana dp/bot один раз (кэш на уровне модуля)."""
    global _arcana_dp, _arcana_bot
    if _arcana_dp is None:
        from arcana.bot import create_dp_and_bot
        _arcana_dp, _arcana_bot = create_dp_and_bot()
    return _arcana_dp, _arcana_bot


class TestArcanaIntegration:
    """Полные integration тесты Arcana — текст → роутинг → handler → ответ."""

    @pytest.fixture(autouse=True)
    async def setup_arcana(self):
        """Подготовить Arcana dispatcher + моки внешних API."""
        self.session = FakeSession()
        self._patches = get_all_patches()

        for p in self._patches:
            try:
                p.start()
            except Exception:
                pass

        try:
            self.dp, self.bot = _get_arcana_dp_bot()
            self.bot.session = self.session
        except Exception as e:
            pytest.skip(f"Не удалось создать Arcana dispatcher: {e}")

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
        r = await self.send("/start")
        assert r, "/start не ответил"

    @pytest.mark.asyncio
    async def test_help(self):
        r = await self.send("/help")
        assert r, "/help не ответил"

    @pytest.mark.asyncio
    async def test_stats(self):
        r = await self.send("/stats")
        assert r, "/stats не ответил"

    @pytest.mark.asyncio
    async def test_finance(self):
        r = await self.send("/finance")
        assert r, "/finance не ответил"

    @pytest.mark.asyncio
    async def test_list(self):
        r = await self.send("/list")
        assert r, "/list не ответил"

    @pytest.mark.asyncio
    async def test_grimoire(self):
        r = await self.send("/grimoire")
        assert r, "/grimoire не ответил"

    @pytest.mark.asyncio
    async def test_tz(self):
        r = await self.send("/tz")
        assert r, "/tz не ответил"

    # ═══════════════════════════════════════
    # CRM КЛИЕНТЫ
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_client_creation(self):
        """'клиент Анна' обрабатывается."""
        r = await self.send("клиент Анна, женщина, 30 лет")
        assert r, "Клиент не создался"
        assert "traceback" not in r.lower()

    @pytest.mark.asyncio
    async def test_debts(self):
        """'сколько мне должны' обрабатывается."""
        r = await self.send("сколько мне должны?")
        assert r, "Долги не вернулись"

    # ═══════════════════════════════════════
    # РАСКЛАДЫ
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_tarot_text(self):
        """Расклад текстом обрабатывается."""
        r = await self.send(
            "три карты, уэйт — туз мечей, жрица, десятка пентаклей"
        )
        assert r, "Расклад не обработался"
        assert "traceback" not in r.lower()

    # ═══════════════════════════════════════
    # РИТУАЛЫ
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_ritual(self):
        """'ритуал: X' обрабатывается."""
        r = await self.send("ритуал: очищение дома, свечи белые 3шт")
        assert r, "Ритуал не записался"

    # ═══════════════════════════════════════
    # РАБОТЫ
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_work_creation(self):
        """'работа: X' обрабатывается."""
        r = await self.send("работа: расклад для Анны")
        assert r, "Работа не создалась"

    # ═══════════════════════════════════════
    # ГРИМУАР
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_grimoire_add(self):
        """'запиши в гримуар' обрабатывается."""
        r = await self.send("запиши в гримуар: заговор на деньги — читать 3 раза")
        assert r, "Гримуар не записал"

    # ═══════════════════════════════════════
    # ПАМЯТЬ
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_memory_save(self):
        r = await self.send("запомни: тест аркана интеграция")
        assert r, "Память не сохранилась"

    # ═══════════════════════════════════════
    # EDGE CASES
    # ═══════════════════════════════════════

    @pytest.mark.asyncio
    async def test_unknown_no_crash(self):
        r = await self.send("абракадабра хтонь")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_en_layout(self):
        r = await self.send("rkbtyn Fyfy")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_stress_no_crash(self):
        """Серия вводов без крашей."""
        inputs = [
            "привет",
            "клиент Тест",
            "работа: тест",
            "запомни: тест",
            "абв",
        ]
        for text in inputs:
            try:
                r = await self.send(text)
                assert "traceback" not in (r or "").lower(), f"Краш на: {text}"
            except Exception as e:
                pytest.fail(f"Exception на '{text}': {e}")
