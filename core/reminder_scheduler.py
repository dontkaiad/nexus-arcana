"""core/reminder_scheduler.py — общий планировщик напоминаний и дедлайнов.

Выделено из nexus/handlers/tasks.py чтобы Arcana могла использовать тот же
APScheduler-flow без дублирования. Nexus tasks.py пока сохраняет своё
внутреннее использование (избежать регрессий); миграция Nexus на этот
модуль — отдельная задача.

Использование:
    flow = ReminderScheduler(callback_prefix="work")
    flow.init(bot, AsyncIOScheduler(timezone=...))
    await flow.schedule_reminder(chat_id, title, "2026-05-05T09:00",
                                 page_id, tz_offset=3)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("core.reminder_scheduler")


def _ensure_datetime(value: str) -> str:
    """'YYYY-MM-DD' → 'YYYY-MM-DDT09:00'; если уже YYYY-MM-DDTHH:MM — возврат как есть."""
    s = (value or "").strip()
    if not s:
        return s
    if "T" in s and len(s) >= 16:
        return s[:16]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s}T09:00"
    return s


class ReminderScheduler:
    """Параметризованный планировщик напоминаний/дедлайнов.

    callback_prefix — префикс для кнопок «Сделано/Перенести/Не сделал», чтобы
    Nexus и Arcana не пересекались (например 'task' vs 'work').
    """

    def __init__(self, callback_prefix: str = "work"):
        self.callback_prefix = callback_prefix
        self._scheduler = None
        self._bot: Optional[Bot] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def init(self, bot: Bot, scheduler) -> None:
        """Привязать bot + APScheduler instance. Scheduler должен быть уже
        запущен (.start())."""
        self._bot = bot
        self._scheduler = scheduler

    @property
    def ready(self) -> bool:
        return self._scheduler is not None and self._bot is not None

    # ── jobs ────────────────────────────────────────────────────────────────

    def remove_jobs(self, page_id: str) -> None:
        if not self._scheduler:
            return
        for prefix in ("reminder_", "deadline_"):
            try:
                self._scheduler.remove_job(f"{prefix}{page_id}")
            except Exception:
                pass

    def _now(self) -> datetime:
        return datetime.now(timezone(timedelta(hours=3)))  # MSK

    def _build_reminder_kb(self, page_id: str) -> InlineKeyboardMarkup:
        cp = self.callback_prefix
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Сделала", callback_data=f"{cp}_complete_{page_id}"),
            InlineKeyboardButton(text="⏭ Перенести", callback_data=f"{cp}_reschedule_{page_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{cp}_delete_{page_id}"),
        ]])

    def _build_deadline_kb(self, page_id: str) -> InlineKeyboardMarkup:
        cp = self.callback_prefix
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"{cp}_complete_{page_id}"),
            InlineKeyboardButton(text="⏳ Отложить", callback_data=f"{cp}_reschedule_{page_id}"),
        ]])

    async def schedule_reminder(
        self,
        chat_id: int,
        title: str,
        reminder_dt: str,
        page_id: str,
        tz_offset: int = 3,
    ) -> bool:
        """Поставить APScheduler-job на reminder_dt (ISO YYYY-MM-DDTHH:MM).
        Возвращает True если запланировано, False если scheduler ещё не
        инициализирован, дата в прошлом и >2мин назад, или ошибка."""
        if not self.ready:
            logger.warning("schedule_reminder skipped: scheduler not ready")
            return False
        try:
            iso = _ensure_datetime(reminder_dt)
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone(timedelta(hours=tz_offset))
            )
        except Exception as e:
            logger.error("schedule_reminder bad dt '%s': %s", reminder_dt, e)
            return False

        bot = self._bot
        kb = self._build_reminder_kb(page_id)

        async def send_reminder() -> None:
            try:
                await bot.send_message(
                    chat_id,
                    f"🔔 <b>Напоминание:</b> {title}\n\nСделано?",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.error("send_reminder failed: %s", e)

        now = self._now()
        if dt <= now:
            missed = (now - dt).total_seconds()
            if missed <= 120:
                logger.info("reminder just passed (%ds), sending now: %s", missed, title)
                await send_reminder()
                return True
            logger.warning("reminder in past (%ds ago), skipping: %s", missed, iso)
            return False

        job_id = f"reminder_{page_id}"
        try:
            self._scheduler.add_job(
                send_reminder, trigger="date", run_date=dt,
                id=job_id, replace_existing=True,
            )
            logger.info("reminder scheduled %s at %s (%s)", page_id[:8], dt, title)
            return True
        except Exception as e:
            logger.error("scheduler.add_job failed: %s", e)
            return False

    async def schedule_deadline_check(
        self,
        chat_id: int,
        title: str,
        deadline_dt: str,
        page_id: str,
        tz_offset: int = 3,
    ) -> bool:
        if not self.ready:
            return False
        try:
            iso = _ensure_datetime(deadline_dt)
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone(timedelta(hours=tz_offset))
            )
        except Exception as e:
            logger.error("schedule_deadline bad dt '%s': %s", deadline_dt, e)
            return False
        if dt <= self._now():
            logger.warning("deadline in past, skipping: %s", iso)
            return False

        bot = self._bot
        kb = self._build_deadline_kb(page_id)

        async def check_deadline() -> None:
            try:
                await bot.send_message(
                    chat_id,
                    f"⏰ <b>Дедлайн:</b> {title}\n\nСделала?",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.error("check_deadline failed: %s", e)

        job_id = f"deadline_{page_id}"
        try:
            self._scheduler.add_job(
                check_deadline, trigger="date", run_date=dt,
                id=job_id, replace_existing=True,
            )
            return True
        except Exception as e:
            logger.error("scheduler.add_job (deadline) failed: %s", e)
            return False
