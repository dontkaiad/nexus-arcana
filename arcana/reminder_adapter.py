"""arcana/reminder_adapter.py — Работа Arcana для общего движка напоминаний.

Вся механика (jobs, «⏰ Пропущено», restore-проходы 1–4, periodic re-arm, проверка
Done/Archived перед отправкой) живёт в core/reminder_scheduler.py и общая с Nexus.
Здесь — только то, чем Работа отличается от Задачи:

  * репозиторий — PgWorksRepo (статусы open/done/archived, «In progress» нет →
    ``on_fire`` — no-op), callback-префикс ``work_`` и его клавиатуры;
  * даты в PG — настоящий UTC (соглашение Nexus, #267) → локальное настенное время;
  * «весь день» у Работы — полночь локального дня: дедлайн-пинг в 09:00;
  * владельцы — по праву ``arcana``; два TG-аккаунта одного user_id — одна группа.

Процессы: бот Arcana (``python -m arcana.bot``) владеет scheduler'ом; Mini App-бэкенд
живёт в процессе Nexus и свой flow не инициализирует. Всё, что Mini App/MCP пишут в PG
(создание/перенос Работы), в Arcana подхватывает periodic re-arm (≤90 с) — отдельных
вызовов планировщика оттуда не нужно.
"""
from __future__ import annotations

from typing import Optional

from core.date_extract import dt_to_local_wall, wall_to_aware
from core.reminder_scheduler import ReminderAdapter, ReminderItem, ReminderScheduler


def work_deadline_wall(deadline_dt, tz_offset: int) -> str:
    """Дедлайн Работы (aware-datetime из PG) → локальная настенная строка: дата без
    времени для «весь день» (полночь локального дня), иначе 'YYYY-MM-DDTHH:MM'."""
    wall = dt_to_local_wall(deadline_dt, tz_offset) if deadline_dt else ""
    return wall[:10] if wall[11:16] == "00:00" else wall


def _repo():
    from arcana.repos.pg_works_repo import PgWorksRepo
    return PgWorksRepo()


class ArcanaReminderAdapter(ReminderAdapter):
    name = "work"
    callback_prefix = "work"
    default_title = "Работа"
    ask_reminder = "Сделано?"
    ask_deadline = "Сделала?"

    async def get(self, item_id: str):
        return await _repo().find_by_id(item_id)

    async def clear_reminder(self, item_id: str) -> None:
        await _repo().clear_reminder(item_id)

    async def set_reminder(self, item_id: str, wall: str, tz_offset: int) -> None:
        await _repo().set_reminder(item_id, wall_to_aware(wall, int(tz_offset)))

    async def shift_series(self, item: ReminderItem, new_reminder_wall: str,
                           new_deadline_day: Optional[str], tz_offset: int) -> None:
        # reschedule_cycle: новые deadline/reminder + статус обратно в 'open'
        await _repo().reschedule_cycle(
            item.id,
            deadline=wall_to_aware(new_deadline_day, int(tz_offset)) if new_deadline_day else None,
            reminder=wall_to_aware(new_reminder_wall, int(tz_offset)),
        )

    async def owner_groups(self):
        # Группируем allowed_ids по user_id: два TG-аккаунта Кай делят один user_id
        # (#202/#306) — один проход на user_id, рассылка во все его чаты.
        from core.config import config as _cfg
        from core.shared_handlers import get_user_tz
        from core.user_manager import get_user
        groups: dict = {}
        tz_by_tg: dict = {}
        for tg_id in _cfg.allowed_ids:
            user_data = await get_user(tg_id)
            if not user_data or not user_data.get("permissions", {}).get("arcana", False):
                continue
            user_id = user_data.get("user_id", "")
            if not user_id:
                continue
            groups.setdefault(user_id, []).append(tg_id)
            try:
                tz_by_tg[tg_id] = int(await get_user_tz(tg_id))
            except Exception:
                tz_by_tg[tg_id] = 3
        return groups, tz_by_tg

    async def fetch_future_reminder(self, user_id: str):
        return await _repo().active_with_future_reminder(user_id)

    async def fetch_past_reminder(self, user_id: str):
        return await _repo().active_with_past_reminder(user_id)

    async def fetch_recurring_no_reminder(self, user_id: str):
        return await _repo().active_recurring_without_reminder(user_id)

    async def fetch_future_deadline_no_reminder(self, user_id: str):
        return await _repo().active_with_future_deadline_no_reminder(user_id)

    def to_item(self, w, tz_offset: int) -> ReminderItem:
        return ReminderItem(
            id=str(w.id),
            title=w.title or self.default_title,
            repeat=w.repeat or "Нет",
            repeat_time=w.repeat_time or "",
            reminder_wall=dt_to_local_wall(w.reminder_dt, tz_offset) if w.reminder_dt else "",
            deadline_wall=work_deadline_wall(w.deadline_dt, tz_offset),   # «весь день» → пинг в 09:00
            duration_min=None,   # works.duration_min — авто-слот Зари (60 мин), не «длительность дела»
            raw=w,
        )


# Один flow на процесс Arcana; bot/scheduler привязываются в arcana.bot.main() через init().
arcana_reminder_flow = ReminderScheduler(adapter=ArcanaReminderAdapter())
