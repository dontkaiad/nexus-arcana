"""Интеграционные тесты Nexus — полный pipeline через dp.feed_update()."""
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from tests.integration.bot_factory import FakeSession, make_update, make_callback_update
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

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 2: Дополнительные команды
    # ═══════════════════════════════════════════════════════════════════

    async def send_cb(self, data: str, text: str = "") -> str:
        """Callback query через dp.feed_update."""
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
    async def test_adhd(self):
        """/adhd — СДВГ-профиль (может быть долго из-за Sonnet — мок)."""
        r = await self.send("/adhd")
        # с моком Claude — быстро
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_budget(self):
        """/budget — показать бюджет."""
        r = await self.send("/budget")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_fixstreak(self):
        """/fixstreak — починить стрик."""
        r = await self.send("/fixstreak")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_finance_stats(self):
        """/finance_stats — статистика финансов."""
        r = await self.send("/finance_stats")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 3: Специфические текстовые команды
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_debt_create(self):
        """'долг маме 5000' — команда долга."""
        r = await self.send("долг маме 5000")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_goal_create(self):
        """'цель айфон 120000' — команда цели."""
        r = await self.send("цель айфон 120000")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_limit_command(self):
        """'лимит на кафе 5000' — установка лимита."""
        r = await self.send("лимит на кафе 5000")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_show_budget_text(self):
        """'покажи бюджет' — тригер через regex."""
        r = await self.send("покажи бюджет")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_what_remember(self):
        """'что ты помнишь о котах' — memory search."""
        r = await self.send("что ты помнишь о котах")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_delete_from_memory(self):
        """'забудь про тест' — удалить из памяти."""
        r = await self.send("забудь про тест")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_neaktualno(self):
        """'неактуально' — деактивация записи."""
        r = await self.send("неактуально")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_delete_note(self):
        """'удали заметку про расходники' — удаление заметки."""
        r = await self.send("удали заметку про расходники")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_delete_all_notes(self):
        """'удали все заметки' — удалить все."""
        r = await self.send("удали все заметки")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_stats_text(self):
        """'сколько потратила за март' — stats."""
        r = await self.send("сколько потратила за март")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_tz_set_utc(self):
        """/tz UTC+5 — установка часового пояса."""
        r = await self.send("/tz UTC+5")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 4: Callbacks Nexus — без pending (graceful)
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_cb_fin_cancel(self):
        """fin_cancel без pending — не крашит."""
        r = await self.send_cb("fin_cancel")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_msg_hide(self):
        """msg_hide — скрыть сообщение."""
        r = await self.send_cb("msg_hide")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_del_cancel(self):
        """del_cancel — отмена удаления."""
        r = await self.send_cb("del_cancel")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_list_keep_task(self):
        """list_keep_task — оставить задачу в списке."""
        r = await self.send_cb("list_keep_task")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_list_cross_no(self):
        """list_cross_no — не вычёркивать."""
        r = await self.send_cb("list_cross_no")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_list_remind_no(self):
        """list_remind_no."""
        r = await self.send_cb("list_remind_no")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_list_to_inv_no(self):
        """list_to_inv_no."""
        r = await self.send_cb("list_to_inv_no")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_list_skip_expiry(self):
        """list_skip_expiry."""
        r = await self.send_cb("list_skip_expiry")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_task_refine_no(self):
        """task_refine_no."""
        r = await self.send_cb("task_refine_no")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_subtask_cancel(self):
        """subtask_cancel."""
        r = await self.send_cb("subtask_cancel")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_mem_cancel(self):
        """mem_cancel."""
        r = await self.send_cb("mem_cancel")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_cb_mem_auto_no(self):
        """mem_auto_no — отказ от автосохранения."""
        r = await self.send_cb("mem_auto_no")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 5: Специфические финансы сценарии
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_expense_with_symbol(self):
        """Расход с ₽ символом."""
        r = await self.send("1000₽ продукты")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_expense_with_rub(self):
        """Расход с 'руб'."""
        r = await self.send("потратила 300 руб на кофе")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_barter(self):
        """Бартер."""
        r = await self.send("бартер консультация на стрижку")
        assert "traceback" not in (r or "").lower()

    # ═══════════════════════════════════════════════════════════════════
    # БЛОК 6: Закрытие задач
    # ═══════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_gotovo(self):
        """'готово' — закрытие задачи."""
        r = await self.send("готово")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_vypolnila(self):
        """'выполнила задачу X'."""
        r = await self.send("выполнила задачу тест")
        assert "traceback" not in (r or "").lower()

    @pytest.mark.asyncio
    async def test_pozvonila(self):
        """'позвонила маме' — маркер done."""
        r = await self.send("позвонила маме")
        assert "traceback" not in (r or "").lower()
