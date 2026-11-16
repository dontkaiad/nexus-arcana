"""core/reminder_scheduler.py — ЕДИНЫЙ движок напоминаний и дедлайнов (Nexus + Arcana).

Одна реализация на оба бота. Различия между Задачей Nexus и Работой Arcana
вынесены в ``ReminderAdapter`` (репозиторий, префикс callback, клавиатуры,
тексты, пояс/получатели). Движок знает только общую механику:

  * ``schedule_reminder`` / ``schedule_deadline_check`` — APScheduler-job на момент
    срабатывания; прошлое ≤120 с шлём сразу, дальше — не планируем (подхватит restore);
    перед отправкой перечитываем статус (Done/Archived → молчим); одноразовое
    напоминание после отправки обнуляется; ``duration_min`` сдвигает дедлайн-пинг
    на конец дела;
  * ``arm`` — как при создании/правке: снять старые jobs, напоминание, а если его
    нет — дедлайн-пинг (issue #69);
  * ``restore`` — проходы 1–4 из PG (джобы живут в памяти и теряются при рестарте):
      1. будущее напоминание → планируем;
      2. прошедшее напоминание → «⏰ Пропущено» (одноразовые: + обнулить reminder;
         повторяющиеся: сдвинуть серию до будущей даты + уведомить);
      3. повтор без reminder → оживить;
      4. будущий дедлайн без reminder → пересобрать дедлайн-пинг (#212);
    ``periodic=True`` — интервальный re-arm (#210): pass 2 для повторяющихся
    пропускаем, иначе sweep через минуту после живого «🔔» пришлёт ещё и «Пропущено».

  * ``shift_cycle`` / ``first_run_wall`` — ЕДИНОЕ место сдвига повторяющейся записи (restore pass 2 и
    «Сделано» обоих ботов): время дедлайна и напоминания сохраняется, «весь день» остаётся днём;

Nexus: ``nexus/handlers/tasks.py`` (``_flow``). Arcana: ``arcana/reminder_adapter.py``.
Паритет двух адаптеров держит ``tests/test_reminder_parity.py`` — расхождение валит тест.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.recurrence import interval_label, next_cycle_date, parse_repeat_time

logger = logging.getLogger("core.reminder_scheduler")

#: Статусы, при которых job молчит (сравнение без учёта регистра: у Задач 'Done', у Работ 'done').
CLOSED_STATUSES = frozenset({"done", "archived"})
#: «Только что прошло» — шлём сразу; старше — не планируем, подхватит restore («Пропущено»).
JUST_PASSED_SEC = 120


def _ensure_datetime(value: Optional[str]) -> str:
    """'YYYY-MM-DD' → 'YYYY-MM-DDT09:00'; если уже YYYY-MM-DDTHH:MM — возврат как есть."""
    s = (value or "").strip()
    if not s:
        return s
    if "T" in s and len(s) >= 16:
        return s[:16]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s}T09:00"
    return s


def _wall_to_dt(wall: str, tz_offset: int) -> datetime:
    return datetime.strptime(_ensure_datetime(wall), "%Y-%m-%dT%H:%M").replace(
        tzinfo=timezone(timedelta(hours=tz_offset))
    )


_HHMM = re.compile(r"^\d{2}:\d{2}$")


def explicit_repeat_time(repeat_time: Optional[str]) -> Optional[str]:
    """Каноническое 'HH:MM' из «Времени повтора», ТОЛЬКО если оно задано явно.

    ``parse_repeat_time`` на пустом значении (и на 'every_2d' без времени) отдаёт 09:00
    по умолчанию — навязывать его записи со своим временем нельзя (напоминание в 18:30
    превращалось бы в 09:00). → None, если явного времени нет."""
    raw = (repeat_time or "").strip()
    if not raw or not _HHMM.match(raw.split("|", 1)[0]):
        return None
    return parse_repeat_time(raw)[0]


def first_run_wall(repeat_time: Optional[str], tz_offset: int, now: Optional[datetime] = None) -> str:
    """Первый будущий запуск серии из «Времени повтора» (по умолчанию 09:00) — локальное
    'YYYY-MM-DDTHH:MM'. Для записи, у которой напоминания ещё нет."""
    time_str, _ = parse_repeat_time(repeat_time or "")
    try:
        h, m = map(int, time_str.split(":"))
    except Exception:
        h, m = 9, 0
    tz = timezone(timedelta(hours=tz_offset))
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    run = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if run <= now_local:
        run += timedelta(days=1)
    return run.strftime("%Y-%m-%dT%H:%M")


def shift_cycle(repeat: str, repeat_time: Optional[str], reminder_wall: str, deadline_wall: str,
                tz_offset: int, now: Optional[datetime] = None) -> Tuple[str, str]:
    """ЕДИНОЕ место сдвига повторяющейся записи на следующий цикл — для Задач и Работ,
    для restore (pass 2) и для «Сделано». Вход/выход — локальные настенные строки.

    * время дедлайна сохраняется ('YYYY-MM-DDTHH:MM' → то же время; дата без времени
      «весь день» остаётся датой);
    * время напоминания сохраняется, кроме случая, когда в «Времени повтора» задано
      явное HH:MM — тогда оно каноническое (возвращает напоминание после снуза);
    * нет напоминания → первый будущий запуск из «Времени повтора» (09:00 по умолчанию);
    * сдвигаем, пока срок не станет строго будущим (напоминание — по минуте, дедлайн — по дню).

    → (новое напоминание или '', новый дедлайн или '')."""
    now = now or datetime.now(timezone.utc)
    tz = timezone(timedelta(hours=tz_offset))
    _, ivl_days = parse_repeat_time(repeat_time or "")
    canon = explicit_repeat_time(repeat_time)

    new_reminder = ""
    if reminder_wall:
        new_reminder = _ensure_datetime(reminder_wall)
        for _ in range(400):
            new_reminder = next_cycle_date(new_reminder, repeat, tz_offset, ivl_days, override_time=canon)
            try:
                if _wall_to_dt(new_reminder[:16], tz_offset) > now:
                    break
            except ValueError:
                break
    elif repeat not in ("", "Нет"):
        new_reminder = first_run_wall(repeat_time, tz_offset, now)

    new_deadline = ""
    if deadline_wall:
        new_deadline = deadline_wall[:16]
        for _ in range(400):
            new_deadline = next_cycle_date(new_deadline, repeat, tz_offset, ivl_days)
            try:
                if datetime.strptime(new_deadline[:10], "%Y-%m-%d").replace(tzinfo=tz) > now:
                    break
            except ValueError:
                break
    return new_reminder, new_deadline


@dataclass
class ReminderItem:
    """Нейтральное представление Задачи/Работы для движка (даты — локальные настенные)."""
    id: str
    title: str
    repeat: str = "Нет"
    repeat_time: str = ""
    reminder_wall: str = ""     # 'YYYY-MM-DDTHH:MM' или ''
    deadline_wall: str = ""     # 'YYYY-MM-DD' (весь день) или 'YYYY-MM-DDTHH:MM' или ''
    duration_min: Optional[int] = None
    raw: Any = None


class ReminderAdapter:
    """Всё, чем Nexus и Arcana различаются. Переопределяется в подклассах."""

    name = "item"
    callback_prefix = "work"
    default_title = "Задача"
    ask_reminder = "Сделано?"
    ask_deadline = "Сделала?"

    def __init__(self, callback_prefix: Optional[str] = None):
        if callback_prefix:
            self.callback_prefix = callback_prefix

    # ── клавиатуры ─────────────────────────────────────────────────────────
    def reminder_kb(self, item_id: str) -> InlineKeyboardMarkup:
        cp = self.callback_prefix
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сделала", callback_data=f"{cp}_complete_{item_id}"),
                InlineKeyboardButton(text="⏭ Перенести", callback_data=f"{cp}_reschedule_{item_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{cp}_delete_{item_id}"),
            ],
            [InlineKeyboardButton(text="⏳ В процессе", callback_data=f"{cp}_wip_{item_id}")],
        ])

    def deadline_kb(self, item_id: str) -> InlineKeyboardMarkup:
        cp = self.callback_prefix
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"{cp}_complete_{item_id}"),
            InlineKeyboardButton(text="⏳ Отложить", callback_data=f"{cp}_reschedule_{item_id}"),
        ]])

    def missed_kb(self, item_id: str) -> InlineKeyboardMarkup:
        return self.reminder_kb(item_id)

    # ── время ──────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return datetime.now(timezone(timedelta(hours=3)))

    # ── чтение/запись состояния (источник истины — PG) ─────────────────────
    async def get(self, item_id: str) -> Any:
        """Текущая запись из PG (для проверки статуса перед отправкой) или None."""
        return None

    def status_of(self, raw: Any) -> str:
        return (getattr(raw, "status", "") or "")

    def repeat_of(self, raw: Any) -> str:
        return (getattr(raw, "repeat", "") or "").strip()

    async def is_muted(self, raw: Any, tz_offset: int) -> bool:
        return False

    async def on_fire(self, item_id: str) -> None:
        """Хук при срабатывании напоминания (Nexus: статус «In progress»)."""

    async def after_sent(self, item_id: str, first_message: Any, title: str) -> None:
        """Хук после успешной отправки (Nexus: запомнить message_id для reply)."""

    async def clear_reminder(self, item_id: str) -> None:
        """Обнулить reminder в PG (одноразовое напоминание отработало)."""

    async def set_reminder(self, item_id: str, wall: str, tz_offset: int) -> None:
        """Записать новое напоминание (проход 3 — оживление серии)."""

    async def shift_series(self, item: ReminderItem, new_reminder_wall: str,
                           new_deadline_wall: Optional[str], tz_offset: int) -> None:
        """Записать сдвинутую серию (проход 2). Даты — из ``shift_cycle``, с сохранённым временем."""

    # ── выборки для restore ────────────────────────────────────────────────
    async def owner_groups(self) -> Tuple[Dict[str, List[int]], Dict[int, int]]:
        """({user_id: [tg_id, ...]}, {tg_id: tz_offset}). Два TG-аккаунта одного
        user_id — одна группа (#202), чтобы job не перетирался replace_existing."""
        return {}, {}

    async def fetch_future_reminder(self, user_id: str) -> List[Any]:
        return []

    async def fetch_past_reminder(self, user_id: str) -> List[Any]:
        return []

    async def fetch_recurring_no_reminder(self, user_id: str) -> List[Any]:
        return []

    async def fetch_future_deadline_no_reminder(self, user_id: str) -> List[Any]:
        return []

    def to_item(self, raw: Any, tz_offset: int) -> ReminderItem:
        raise NotImplementedError


class ReminderScheduler:
    """Движок напоминаний/дедлайнов. Один экземпляр на бота."""

    def __init__(self, callback_prefix: str = "work", adapter: Optional[ReminderAdapter] = None):
        self.adapter = adapter or ReminderAdapter(callback_prefix)
        self._scheduler = None
        self._bot: Optional[Bot] = None
        self._bot_getter: Optional[Callable[[], Any]] = None
        self._scheduler_getter: Optional[Callable[[], Any]] = None

    @property
    def callback_prefix(self) -> str:
        return self.adapter.callback_prefix

    # ── lifecycle ──────────────────────────────────────────────────────────

    def init(self, bot: Bot, scheduler) -> None:
        """Привязать bot + APScheduler instance (scheduler уже запущен)."""
        self._bot = bot
        self._scheduler = scheduler

    def bind(self, bot_getter: Callable[[], Any], scheduler_getter: Callable[[], Any]) -> None:
        """Ленивая привязка: bot/scheduler берутся при каждом вызове (Nexus держит их
        в глобалах tasks.py, которые init_scheduler присваивает уже после импорта)."""
        self._bot_getter = bot_getter
        self._scheduler_getter = scheduler_getter

    @property
    def bot(self):
        return self._bot_getter() if self._bot_getter else self._bot

    @property
    def scheduler(self):
        return self._scheduler_getter() if self._scheduler_getter else self._scheduler

    @property
    def ready(self) -> bool:
        return self.scheduler is not None and self.bot is not None

    # ── jobs ────────────────────────────────────────────────────────────────

    def remove_jobs(self, page_id: str) -> None:
        sched = self.scheduler
        if not sched:
            return
        for prefix in ("reminder_", "deadline_"):
            try:
                sched.remove_job(f"{prefix}{page_id}")
                logger.info("removed job %s%s", prefix, page_id)
            except Exception:
                pass

    def _build_reminder_kb(self, page_id: str) -> InlineKeyboardMarkup:
        return self.adapter.reminder_kb(page_id)

    def _build_deadline_kb(self, page_id: str) -> InlineKeyboardMarkup:
        return self.adapter.deadline_kb(page_id)

    async def _is_closed(self, item_id: str, who: str) -> Tuple[bool, Any]:
        """Перечитать запись: колонки БД — источник истины, job может протухнуть
        (закрыли из Mini App уже после постановки). → (закрыта?, запись|None)."""
        if not item_id:
            return False, None
        try:
            cur = await self.adapter.get(item_id)
        except Exception as e:
            logger.warning("%s: status check failed: %s", who, e)
            return False, None
        if cur is not None and self.adapter.status_of(cur).strip().lower() in CLOSED_STATUSES:
            logger.info("%s: %s already %s, skipping", who, item_id, self.adapter.status_of(cur))
            return True, cur
        return False, cur

    async def _fan_out(self, targets: List[int], text: str, kb: InlineKeyboardMarkup, who: str):
        """Рассылка во все чаты владельца. → первое успешно отправленное сообщение | None."""
        bot = self.bot
        first = None
        for target in targets:
            try:
                msg = await bot.send_message(target, text, parse_mode="HTML", reply_markup=kb)
                if first is None:
                    first = msg
            except Exception as e:
                logger.warning("%s: send to %s failed: %s", who, target, e)
        return first

    # ── reminder ────────────────────────────────────────────────────────────

    async def schedule_reminder(
        self,
        chat_id: int,
        title: str,
        reminder_dt: str,
        page_id: str,
        tz_offset: int = 3,
        recipients: Optional[List[int]] = None,
    ) -> bool:
        """Поставить job на reminder_dt ('YYYY-MM-DDTHH:MM', локальное время).

        ``recipients`` — все tg_id владельца (#202/#306); по умолчанию только
        ``chat_id``. Возвращает True если запланировано/отправлено сразу, False если
        scheduler не готов, время не разобрать, оно в прошлом >2 мин или ошибка."""
        if not self.ready:
            logger.warning("schedule_reminder skipped: scheduler not ready")
            return False
        ad = self.adapter
        targets = [t for t in (recipients or [chat_id]) if t]
        try:
            dt = _wall_to_dt(reminder_dt, tz_offset)
        except Exception as e:
            logger.error("schedule_reminder bad dt '%s': %s", reminder_dt, e)
            return False

        async def send_reminder() -> None:
            closed, cur = await self._is_closed(page_id, "send_reminder")
            if closed:
                return
            if cur is not None and await ad.is_muted(cur, tz_offset):
                logger.info("send_reminder: %s muted by adapter", page_id)
                return
            try:
                await ad.on_fire(page_id)
            except Exception as e:
                logger.warning("send_reminder: on_fire failed: %s", e)
            first = await self._fan_out(
                targets,
                f"🔔 <b>Напоминание:</b> {title}\n\n{ad.ask_reminder}",
                ad.reminder_kb(page_id), "send_reminder",
            )
            if first is None:
                logger.error("send_reminder: all targets failed for %s", page_id)
                return
            try:
                await ad.after_sent(page_id, first, title)
            except Exception as e:
                logger.warning("send_reminder: after_sent failed: %s", e)
            # Одноразовое напоминание отработало — обнуляем колонку, иначе при
            # следующем restore pass 2 пришлёт «Пропущено» дубль. Повторяющиеся не
            # трогаем — их reminder двигает обработчик «Сделано».
            if page_id and ad.repeat_of(cur) in ("", "Нет"):
                try:
                    await ad.clear_reminder(page_id)
                except Exception as e:
                    logger.warning("send_reminder: clear_reminder failed: %s", e)

        try:
            now = ad.now()
            if dt <= now:
                missed = (now - dt).total_seconds()
                if missed <= JUST_PASSED_SEC:
                    logger.info("reminder just passed (%ds), sending now: %s", missed, title)
                    await send_reminder()
                    return True
                logger.warning("reminder in past (%ds ago), skipping: %s", missed, reminder_dt)
                return False
            job_id = f"reminder_{page_id}" if page_id else f"rem_{chat_id}_{title[:15]}_{reminder_dt}"
            self.scheduler.add_job(send_reminder, trigger="date", run_date=dt,
                                   id=job_id, replace_existing=True)
            logger.info("reminder scheduled %s at %s (%s)", page_id, dt, title)
            return True
        except Exception as e:
            logger.error("schedule reminder error: %s", e)
            return False

    # ── deadline ────────────────────────────────────────────────────────────

    async def schedule_deadline_check(
        self,
        chat_id: int,
        title: str,
        deadline_dt: str,
        page_id: str,
        tz_offset: int = 3,
        recipients: Optional[List[int]] = None,
        duration_min: Optional[int] = None,
    ) -> bool:
        """Дедлайн-пинг. 'YYYY-MM-DD' (весь день) → 09:00. ``duration_min``: дедлайн —
        начало дела, спрашиваем «Сделал?» в конце (#241)."""
        if not self.ready:
            return False
        ad = self.adapter
        targets = [t for t in (recipients or [chat_id]) if t]
        try:
            dt = _wall_to_dt(deadline_dt, tz_offset)
            if duration_min:
                dt = dt + timedelta(minutes=duration_min)
        except Exception as e:
            logger.error("schedule_deadline bad dt '%s': %s", deadline_dt, e)
            return False
        if dt <= ad.now():
            logger.warning("deadline in past, skipping: %s", deadline_dt)
            return False

        async def check_deadline() -> None:
            closed, _ = await self._is_closed(page_id, "check_deadline")
            if closed:
                return
            first = await self._fan_out(
                targets,
                f"⏰ <b>Дедлайн:</b> {title}\n\n{ad.ask_deadline}",
                ad.deadline_kb(page_id), "check_deadline",
            )
            if first is None:
                logger.error("check_deadline: all targets failed for %s", page_id)
                return
            try:
                await ad.after_sent(page_id, first, title)
            except Exception as e:
                logger.warning("check_deadline: after_sent failed: %s", e)

        job_id = f"deadline_{page_id}" if page_id else f"deadline_{chat_id}_{title[:15]}_{deadline_dt}"
        try:
            self.scheduler.add_job(check_deadline, trigger="date", run_date=dt,
                                   id=job_id, replace_existing=True)
            logger.info("deadline check scheduled %s at %s", page_id, dt)
            return True
        except Exception as e:
            logger.error("scheduler.add_job (deadline) failed: %s", e)
            return False

    # ── create / edit ───────────────────────────────────────────────────────

    async def arm(
        self,
        item_id: str,
        title: str,
        *,
        chat_id: int,
        tz_offset: int = 3,
        reminder_wall: Optional[str] = None,
        deadline_wall: Optional[str] = None,
        duration_min: Optional[int] = None,
        recipients: Optional[List[int]] = None,
        replace: bool = True,
    ) -> str:
        """Как при создании/правке: снять старые jobs, затем напоминание, а если его
        нет — дедлайн-пинг (issue #69: при наличии reminder пинг по дедлайну не нужен).
        → 'reminder' | 'deadline' | '' (нечего планировать / не вышло)."""
        if replace:
            self.remove_jobs(item_id)
        if reminder_wall:
            ok = await self.schedule_reminder(chat_id, title, reminder_wall, item_id, tz_offset,
                                              recipients=recipients)
            return "reminder" if ok else ""
        if deadline_wall:
            ok = await self.schedule_deadline_check(chat_id, title, deadline_wall, item_id, tz_offset,
                                                    recipients=recipients, duration_min=duration_min)
            return "deadline" if ok else ""
        return ""

    # ── restore (старт + periodic re-arm) ───────────────────────────────────

    async def restore(self, periodic: bool = False) -> int:
        """Восстановить jobs из PG, проходы 1–4 (см. модульный docstring)."""
        if not self.ready:
            logger.warning("restore: scheduler/bot not ready")
            return 0
        ad = self.adapter
        try:
            groups, tz_by_tg = await ad.owner_groups()
        except Exception as e:
            logger.error("restore: owner_groups failed: %s", e)
            return 0
        restored = 0
        for user_id, tgids in groups.items():
            tg_id = tgids[0]  # primary chat для логов и ключа job
            tz = tz_by_tg.get(tg_id, 3)
            for name, fn in (
                ("pass1", self._restore_future),
                ("pass2", self._restore_missed),
                ("pass3", self._restore_revive),
                ("pass4", self._restore_deadlines),
            ):
                try:
                    restored += await fn(user_id, tg_id, tgids, tz, periodic)
                except Exception as e:
                    logger.error("restore %s: user %s failed: %s", name, user_id, e)
        logger.info("restore[%s]: restored %d jobs", ad.name, restored)
        return restored

    async def _restore_future(self, user_id, tg_id, tgids, tz, periodic) -> int:
        """Проход 1: будущие напоминания — планируем как есть (идемпотентно)."""
        ad, n = self.adapter, 0
        for raw in await ad.fetch_future_reminder(user_id):
            try:
                if await ad.is_muted(raw, tz):
                    continue
                item = ad.to_item(raw, tz)
                if item.reminder_wall:
                    await self.schedule_reminder(tg_id, item.title, item.reminder_wall,
                                                 item.id, tz, recipients=tgids)
                    n += 1
            except Exception as e:
                logger.error("restore pass1: %s error: %s", getattr(raw, "id", "?"), e)
        return n

    async def _restore_missed(self, user_id, tg_id, tgids, tz, periodic) -> int:
        """Проход 2: прошедшее напоминание — «⏰ Пропущено»."""
        ad, n = self.adapter, 0
        now_utc = datetime.now(timezone.utc)
        for raw in await ad.fetch_past_reminder(user_id):
            try:
                if await ad.is_muted(raw, tz):
                    continue
                item = ad.to_item(raw, tz)
                if not item.reminder_wall:
                    continue
                try:
                    missed_time = _wall_to_dt(item.reminder_wall, tz).strftime("%d.%m в %H:%M")
                except ValueError:
                    missed_time = item.reminder_wall[:16]
                repeat = item.repeat

                if repeat in ("", "Нет"):
                    # Одноразовая — «Пропущено» СРАЗУ, затем обнулить reminder (иначе
                    # следующий рестарт пришлёт дубль).
                    try:
                        await ad.on_fire(item.id)
                    except Exception as e:
                        logger.warning("restore pass2: on_fire failed for '%s': %s", item.title, e)
                    first = await self._fan_out(
                        tgids,
                        f"⏰ <b>Пропущено ({missed_time}):</b> {item.title}\n\n{ad.ask_reminder}",
                        ad.missed_kb(item.id), "restore pass2",
                    )
                    if first is None:
                        logger.error("restore pass2: all targets failed for missed '%s'", item.title)
                        continue
                    await ad.after_sent(item.id, first, item.title)
                    await ad.clear_reminder(item.id)
                    logger.info("restore pass2: sent missed '%s' (was at %s)", item.title, missed_time)
                    n += 1
                    continue

                if periodic:
                    continue  # повторяющиеся в periodic не трогаем — подхватит рестарт

                # Повторяющаяся: СНАЧАЛА сдвинуть серию в PG, потом уведомить — если
                # процесс перезапустят между send и записью, следующий restore увидит
                # future-reminder (pass 1), а не продублирует «Пропущено» (#206).
                _, ivl_days = parse_repeat_time(item.repeat_time)
                repeat_display = interval_label(ivl_days) if ivl_days > 1 else repeat
                new_reminder, new_deadline = shift_cycle(
                    repeat, item.repeat_time, item.reminder_wall, item.deadline_wall, tz, now_utc)
                await ad.shift_series(item, new_reminder, new_deadline or None, tz)
                await self.schedule_reminder(tg_id, item.title, new_reminder, item.id, tz,
                                             recipients=tgids)
                first = await self._fan_out(
                    tgids,
                    f"⏰ <b>Пропущено ({missed_time}):</b> {item.title}\n"
                    f"🔄 Повтор: {repeat_display} — переношу.\n\n{ad.ask_reminder}",
                    ad.missed_kb(item.id), "restore pass2",
                )
                if first is not None:
                    await ad.after_sent(item.id, first, item.title)
                logger.info("restore pass2: rescheduled '%s' repeat=%s next=%s",
                            item.title, repeat, new_reminder)
                n += 1
            except Exception as e:
                logger.error("restore pass2: %s error: %s", getattr(raw, "id", "?"), e)
        return n

    async def _restore_revive(self, user_id, tg_id, tgids, tz, periodic) -> int:
        """Проход 3: repeat_time есть, reminder IS NULL — оживляем (первый future-run)."""
        ad, n = self.adapter, 0
        for raw in await ad.fetch_recurring_no_reminder(user_id):
            try:
                item = ad.to_item(raw, tz)
                if not item.repeat_time:
                    continue
                new_reminder = first_run_wall(item.repeat_time, tz)
                await ad.set_reminder(item.id, new_reminder, tz)
                await self.schedule_reminder(tg_id, item.title, new_reminder, item.id, tz,
                                             recipients=tgids)
                logger.info("restore pass3: revived '%s' repeat_time=%s next=%s",
                            item.title, item.repeat_time, new_reminder)
                n += 1
            except Exception as e:
                logger.error("restore pass3: %s error: %s", getattr(raw, "id", "?"), e)
        return n

    async def _restore_deadlines(self, user_id, tg_id, tgids, tz, periodic) -> int:
        """Проход 4: будущий дедлайн без напоминания — пересобрать дедлайн-пинг (#212)."""
        ad, n = self.adapter, 0
        for raw in await ad.fetch_future_deadline_no_reminder(user_id):
            try:
                item = ad.to_item(raw, tz)
                if not item.deadline_wall:
                    continue
                # Момент пинга (дедлайн + длительность) уже прошёл — молча пропускаем,
                # иначе periodic re-arm писал бы предупреждение каждые 90 с.
                if _wall_to_dt(item.deadline_wall, tz) + timedelta(minutes=item.duration_min or 0) <= ad.now():
                    continue
                await self.schedule_deadline_check(tg_id, item.title, item.deadline_wall, item.id, tz,
                                                   recipients=tgids, duration_min=item.duration_min)
                n += 1
            except Exception as e:
                logger.error("restore pass4: %s error: %s", getattr(raw, "id", "?"), e)
        return n
