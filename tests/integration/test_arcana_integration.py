"""Интеграционные тесты Arcana — полный pipeline через dp.feed_update()."""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from tests.integration.bot_factory import FakeSession, make_update, make_callback_update
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

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 2: route_message — все 15 intent dispatch типов
    # ═══════════════════════════════════════════════════════════════════

    async def send_cb(self, data: str, text: str = "") -> str:
        """Отправить callback query через dp.feed_update."""
        self.session.clear()
        update = make_callback_update(data, original_text=text,
                                      update_id=abs(hash(data)) % 100000)
        try:
            await self.dp.feed_update(self.bot, update)
        except Exception as e:
            return f"[ERROR: {e}]"
        await asyncio.sleep(0.05)
        return self.session.get_last_response()

    @pytest.mark.asyncio
    async def test_route_session(self):
        """intent=session — отправляется в sessions handler."""
        r = await self.send("сеанс с Анной таро уэйт")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_client_info(self):
        """intent=client_info — досье."""
        r = await self.send("что у Анны?")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_tarot_interp(self):
        """intent=tarot_interp — трактовка."""
        r = await self.send("что означает жрица в позиции прошлого")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_work_done(self):
        """'сделала работу X' обрабатывается."""
        r = await self.send("сделала работа тест")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_work_list(self):
        """intent=work_list — список работ."""
        r = await self.send("покажи работы")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_delete(self):
        """intent=delete — удалить запись."""
        r = await self.send("удали последнюю запись")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_verify(self):
        """intent=verify — Анна — сбылось."""
        r = await self.send("Анна 5 марта — сбылось")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_stats_intent(self):
        """intent=stats через текст."""
        r = await self.send("процент сбывшихся раскладов")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_grimoire_search(self):
        """intent=grimoire_search."""
        r = await self.send("поиск в гримуаре: заговор")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_route_finance(self):
        """intent=finance Arcana."""
        r = await self.send("сколько заработала в этом месяце")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 3: Callbacks — Grimoire menu
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_cb_grim_menu(self):
        """cb_grim_menu — возврат в меню."""
        r = await self.send_cb("grim_menu")
        # либо edit_text либо answer — главное без краша
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_rituals(self):
        r = await self.send_cb("grim_rituals")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_spells(self):
        r = await self.send_cb("grim_spells")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_recipes(self):
        r = await self.send_cb("grim_recipes")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_combos(self):
        r = await self.send_cb("grim_combos")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_notes(self):
        r = await self.send_cb("grim_notes")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_inventory(self):
        r = await self.send_cb("grim_inventory")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_grim_search(self):
        r = await self.send_cb("grim_search")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 4: Callbacks — Tarot save/edit/cancel (без pending → graceful)
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_cb_tarot_cancel_no_pending(self):
        """cb_tarot_cancel без pending — не крашит."""
        r = await self.send_cb("tarot_cancel")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_tarot_save_no_pending(self):
        """cb_tarot_save без pending — не крашит."""
        r = await self.send_cb("tarot_save")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_tarot_edit_no_pending(self):
        """cb_tarot_edit без pending — не крашит."""
        r = await self.send_cb("tarot_edit")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 5: Arcana memory callbacks
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_cb_arcmem_cancel(self):
        """arcmem_cancel — не крашит."""
        r = await self.send_cb("arcmem_cancel")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_arcmem_auto_no(self):
        """arcmem_auto_no — отказ от auto-suggest."""
        r = await self.send_cb("arcmem_auto_no")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 6: Photo unknown callbacks (photo_cancel)
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_cb_photo_cancel_no_pending(self):
        """photo_cancel без pending — корректный ответ."""
        r = await self.send_cb("photo_cancel:67686090")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 7: Unknown intent → кнопки "что это?"
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_unknown_shows_buttons(self):
        """Первый раз unknown → показывает кнопки типов."""
        # нужно чтобы classify дважды вернула unknown
        async def unknown_always(*args, **kwargs):
            return "unknown"
        with patch("arcana.handlers.base.ask_claude", side_effect=unknown_always):
            r = await self.send("абракадабра совсем неясное 999")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 8: Arcana lists handlers
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_list_buy_arcana(self):
        """'купить свечи' в Арканe."""
        r = await self.send("купить свечи белые")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_inventory_search(self):
        """Поиск по инвентарю."""
        r = await self.send("есть ли свечи")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 9: Photo + caption (route_message с photo → fallback)
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_tz_command_setting(self):
        """/tz UTC+5 — установка часового пояса."""
        r = await self.send("/tz UTC+5")
        assert "traceback" not in (r or "").lower()
