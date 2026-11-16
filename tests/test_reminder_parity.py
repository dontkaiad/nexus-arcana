"""tests/test_reminder_parity.py — напоминания и дедлайны Работ Arcana == Задачи Nexus.

ОДИН набор сценариев гоняется на реальных адаптерах обоих ботов поверх общего движка
(core/reminder_scheduler.py). На границе репозиториев стоят подмены (PG не нужен):
Nexus — PgTasksRepo/TasksRepo, Arcana — PgWorksRepo. Из каждого прогона берётся один и
тот же нормализованный результат: какие jobs поставлены (вид + локальное время), что
и кому отправлено (шапка сообщения), что обнулено в PG. Результаты ДОЛЖНЫ совпасть;
расхождение валит тест — значит, у ботов появилась вторая реализация.

Сценарии: будущее; прошлое ≤2 мин; прошлое после рестарта; дедлайн без reminder;
закрыто до срабатывания; + «весь день», повтор и periodic re-arm. Получатели — два
TG-аккаунта одного владельца (#202): уведомление уходит в оба.

Сдвиг повторяющейся серии (restore pass 2 и «Сделано») проверяется по тому, что реально
записано в PG: время дедлайна и напоминания обязано сохраняться, «весь день» — остаться днём.

Клавиатуры различаются по замыслу (префикс callback ``task_`` / ``work_``) — это
проверяется отдельно и в сравнение не входит.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MSK = timezone(timedelta(hours=3))
TZ = 3
TG_IDS = [111, 222]            # два аккаунта одного user_id (#202)
ITEM_ID = "7"
TITLE = "Сверить расклад"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _wall(dt: datetime) -> str:
    return dt.astimezone(MSK).strftime("%Y-%m-%dT%H:%M")


# ── общая модель: что лежит в PG ────────────────────────────────────────────

@dataclass
class Spec:
    id: str = ITEM_ID
    title: str = TITLE
    reminder: Optional[datetime] = None      # aware UTC, как отдаёт PG
    deadline: Optional[datetime] = None
    all_day: bool = False                    # дедлайн без времени («до пятницы»)
    status: str = "open"                     # open | done
    repeat: str = "Нет"
    repeat_time: str = ""

    def is_open(self) -> bool:
        return self.status == "open"


class _FakeScheduler:
    def __init__(self):
        self.jobs: dict = {}

    def add_job(self, func, trigger=None, run_date=None, id=None, replace_existing=False, **kw):
        self.jobs[id] = SimpleNamespace(func=func, run_date=run_date)

    def remove_job(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        del self.jobs[job_id]


class _FakeBot:
    def __init__(self):
        self.sent: list = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=len(self.sent))


# ── харнессы: один интерфейс, два бота ──────────────────────────────────────

class _Harness:
    name = ""

    def __init__(self):
        self.specs: dict = {}
        self.cleared: set = set()
        self.written: dict = {}      # что записано в PG при сдвиге серии: {"reminder": wall, "deadline": wall}
        self.sched = _FakeScheduler()
        self.bot = _FakeBot()

    # выборки — те же условия, что в реальных SQL-запросах обоих репозиториев
    def _future_reminder(self, _uid=""):
        return [s for s in self.specs.values() if s.is_open() and s.reminder and s.reminder > _now()]

    def _past_reminder(self, _uid=""):
        return [s for s in self.specs.values() if s.is_open() and s.reminder and s.reminder < _now()]

    def _recurring_no_reminder(self, _uid=""):
        return [s for s in self.specs.values() if s.is_open() and s.repeat_time and not s.reminder]

    def _future_deadline_no_reminder(self, _uid=""):
        return [s for s in self.specs.values()
                if s.is_open() and s.deadline and s.deadline > _now() and not s.reminder]

    def _record_written(self, reminder_wall: str = "", deadline_wall: str = "") -> None:
        """Сдвиг серии → нормализованная запись. «Весь день» у Задачи — дата, у Работы — локальная
        полночь: приводим к одному виду (дата без времени)."""
        norm = lambda w: w[:10] if w.endswith("T00:00") else w   # noqa: E731
        if reminder_wall:
            self.written["reminder"] = norm(reminder_wall)
        if deadline_wall:
            self.written["deadline"] = norm(deadline_wall)

    def _clear(self, item_id):
        self.cleared.add(str(item_id))
        if str(item_id) in self.specs:
            self.specs[str(item_id)].reminder = None
        return True


class NexusHarness(_Harness):
    name = "nexus"

    @property
    def flow(self):
        from nexus.handlers import tasks
        return tasks._flow

    def _task(self, s: Spec):
        from nexus.repos.pg_tasks_repo import Task
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S+00:00") if d else ""   # noqa: E731
        return Task(
            id=s.id, title=s.title, status="Done" if s.status == "done" else "Not started",
            repeat=s.repeat, repeat_time=s.repeat_time,
            reminder=iso(s.reminder), deadline=iso(s.deadline), deadline_all_day=s.all_day,
        )

    def patches(self, stack: ExitStack) -> None:
        from nexus.handlers import tasks
        import nexus.repos.pg_tasks_repo as pgt
        import nexus.repos.tasks_repo as trepo
        wrap = lambda f: AsyncMock(side_effect=lambda uid="": [self._task(s) for s in f()])   # noqa: E731

        async def retrieve(page_id):
            s = self.specs.get(str(page_id))
            return self._task(s) if s else None

        async def set_props(_page_id, props):
            def wall(prop):
                start = ((props.get(prop) or {}).get("date") or {}).get("start", "")
                if "T" not in start:
                    return start
                return _wall(datetime.fromisoformat(start))
            self._record_written(wall("Напоминание"), wall("Дедлайн"))

        for target, attr, val in [
            (tasks, "_scheduler", self.sched),
            (tasks, "_bot", self.bot),
            (tasks, "_get_user_tz", AsyncMock(return_value=TZ)),
            (tasks, "save_task_reminder", AsyncMock()),
            (pgt.PgTasksRepo, "active_with_future_reminder", wrap(self._future_reminder)),
            (pgt.PgTasksRepo, "active_with_past_reminder", wrap(self._past_reminder)),
            (pgt.PgTasksRepo, "active_recurring_without_reminder", wrap(self._recurring_no_reminder)),
            (pgt.PgTasksRepo, "active_with_future_deadline_no_reminder",
             wrap(self._future_deadline_no_reminder)),
            (pgt.PgTasksRepo, "clear_reminder", AsyncMock(side_effect=lambda tid: self._clear(tid))),
            (trepo.TasksRepo, "retrieve_page", AsyncMock(side_effect=retrieve)),
            (trepo.TasksRepo, "set_in_progress", AsyncMock()),
            (trepo.TasksRepo, "set_props", AsyncMock(side_effect=set_props)),
        ]:
            stack.enter_context(patch.object(target, attr, val))

    async def complete_recurring(self, spec: Spec) -> None:
        """«Сделано» по повторяющейся Задаче — родной обработчик Nexus."""
        from nexus.handlers import tasks
        msg = MagicMock()
        msg.answer = AsyncMock()
        msg.chat.id = TG_IDS[0]
        await tasks._handle_recurring_task_reset(
            msg, spec.id, self._task(spec), spec.repeat, spec.title, TG_IDS[0])


class ArcanaHarness(_Harness):
    name = "arcana"

    @property
    def flow(self):
        from arcana.reminder_adapter import arcana_reminder_flow
        return arcana_reminder_flow

    def _work(self, s: Spec):
        from arcana.repos.works_repo import Work
        deadline = s.deadline
        if s.all_day and deadline:
            # «Весь день» у Работы — полночь ЛОКАЛЬНОГО дня (#272); у Задачи Nexus — UTC-полночь
            # даты + флаг deadline_all_day. Каждому боту отдаём его родное представление ТОГО ЖЕ дня.
            deadline = datetime(deadline.year, deadline.month, deadline.day, tzinfo=MSK)
        return Work(
            id=s.id, title=s.title, priority="Важно", deadline_str="", category_str="",
            has_client=False, status="done" if s.status == "done" else "open",
            deadline_dt=deadline, reminder_dt=s.reminder, repeat=s.repeat, repeat_time=s.repeat_time,
        )

    def patches(self, stack: ExitStack) -> None:
        import arcana.repos.pg_works_repo as pgw
        flow = self.flow
        wrap = lambda f: AsyncMock(side_effect=lambda uid="": [self._work(s) for s in f()])   # noqa: E731

        async def find(work_id):
            s = self.specs.get(str(work_id))
            return self._work(s) if s else None

        async def reschedule_cycle(_work_id, deadline=None, reminder=None):
            self._record_written(_wall(reminder) if reminder else "", _wall(deadline) if deadline else "")

        async def set_reminder(_work_id, reminder):
            self._record_written(_wall(reminder))

        for target, attr, val in [
            (flow, "_scheduler", self.sched),
            (flow, "_bot", self.bot),
            (pgw.PgWorksRepo, "active_with_future_reminder", wrap(self._future_reminder)),
            (pgw.PgWorksRepo, "active_with_past_reminder", wrap(self._past_reminder)),
            (pgw.PgWorksRepo, "active_recurring_without_reminder", wrap(self._recurring_no_reminder)),
            (pgw.PgWorksRepo, "active_with_future_deadline_no_reminder",
             wrap(self._future_deadline_no_reminder)),
            (pgw.PgWorksRepo, "clear_reminder", AsyncMock(side_effect=lambda wid: self._clear(wid))),
            (pgw.PgWorksRepo, "find_by_id", AsyncMock(side_effect=find)),
            (pgw.PgWorksRepo, "set_reminder", AsyncMock(side_effect=set_reminder)),
            (pgw.PgWorksRepo, "reschedule_cycle", AsyncMock(side_effect=reschedule_cycle)),
        ]:
            stack.enter_context(patch.object(target, attr, val))
        stack.enter_context(patch("core.shared_handlers.get_user_tz", AsyncMock(return_value=TZ)))
        stack.enter_context(patch("core.user_manager.get_user_id", AsyncMock(return_value="u-1")))
        stack.enter_context(patch("core.user_manager.get_tg_ids_for_user", AsyncMock(return_value=TG_IDS)))

    async def complete_recurring(self, spec: Spec) -> None:
        """«Сделала» по повторяющейся Работе — родной обработчик Arcana."""
        from arcana.handlers.work_reminder_kb import _handle_recurring_work_done
        msg = MagicMock()
        msg.chat.id = TG_IDS[0]
        msg.from_user.id = TG_IDS[0]
        await _handle_recurring_work_done(msg, spec.id, self._work(spec), spec.title, TZ)


HARNESSES = {"nexus": NexusHarness, "arcana": ArcanaHarness}


async def _run(system: str, scenario) -> dict:
    """Прогнать сценарий на одном боте → нормализованный результат."""
    from core.config import config
    h = HARNESSES[system]()
    user = {"permissions": {"nexus": True, "arcana": True}, "user_id": "u-1"}
    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "allowed_ids", TG_IDS))
        stack.enter_context(patch("core.user_manager.get_user", AsyncMock(return_value=user)))
        h.patches(stack)
        await scenario(h)
        return {
            # какие jobs стоят: (вид, локальное время срабатывания)
            "jobs": sorted((jid.split("_")[0], _wall(j.run_date)) for jid, j in h.sched.jobs.items()),
            # что ушло: (получатель, шапка сообщения без вопроса «Сделано?»)
            "sent": [(chat, text.split("\n\n")[0]) for chat, text, _kb in h.bot.sent],
            "cleared": sorted(h.cleared),
            **({"written": dict(h.written)} if h.written else {}),
        }


async def _fire_all(h: _Harness) -> None:
    for job in list(h.sched.jobs.values()):
        await job.func()


# ── сценарии: общие для обоих ботов ─────────────────────────────────────────

async def sc_future_reminder(h):
    """Будущее напоминание после рестарта → job на локальное время, ничего не отправлено."""
    h.specs[ITEM_ID] = Spec(reminder=_now() + timedelta(hours=3))
    await h.flow.restore()


async def sc_reminder_just_passed(h):
    """Прошло ≤2 мин → отправляем сразу (не планируем), одноразовое напоминание обнуляем."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(seconds=30))
    await h.flow.schedule_reminder(TG_IDS[0], TITLE, _wall(h.specs[ITEM_ID].reminder), ITEM_ID, TZ,
                                   recipients=TG_IDS)


async def sc_reminder_past_not_scheduled(h):
    """Прошло >2 мин при постановке → job не ставится (это дело restore), ничего не уходит."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(hours=3))
    await h.flow.schedule_reminder(TG_IDS[0], TITLE, _wall(h.specs[ITEM_ID].reminder), ITEM_ID, TZ,
                                   recipients=TG_IDS)


async def sc_past_after_restart(h):
    """Прошло после рестарта → «⏰ Пропущено» (не молчаливая потеря) в оба чата, reminder обнулён."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(hours=3))
    await h.flow.restore()


async def sc_past_after_restart_not_repeated(h):
    """Второй рестарт/sweep «Пропущено» не дублирует: reminder уже обнулён."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(hours=3))
    await h.flow.restore()
    await h.flow.restore(periodic=True)


async def sc_deadline_without_reminder(h):
    """Будущий дедлайн без reminder → после рестарта дедлайн-пинг пересобран."""
    h.specs[ITEM_ID] = Spec(deadline=_now() + timedelta(hours=4))
    await h.flow.restore()


async def sc_deadline_all_day(h):
    """Дедлайн «весь день» → пинг в 09:00 локального дня."""
    d = (_now().astimezone(MSK) + timedelta(days=3)).date()
    h.specs[ITEM_ID] = Spec(deadline=datetime(d.year, d.month, d.day, tzinfo=timezone.utc), all_day=True)
    await h.flow.restore()


async def sc_deadline_fires(h):
    """Сработавший дедлайн-пинг отправляется в оба чата."""
    h.specs[ITEM_ID] = Spec(deadline=_now() + timedelta(hours=4))
    await h.flow.restore()
    await _fire_all(h)


async def sc_reminder_fires_and_clears(h):
    """Сработавшее одноразовое напоминание: «🔔» в оба чата + обнулён reminder."""
    h.specs[ITEM_ID] = Spec(reminder=_now() + timedelta(hours=3))
    await h.flow.restore()
    await _fire_all(h)


async def sc_closed_before_reminder_fires(h):
    """Закрыто (Done) уже после постановки job → при срабатывании молчим."""
    h.specs[ITEM_ID] = Spec(reminder=_now() + timedelta(hours=3))
    await h.flow.restore()
    h.specs[ITEM_ID].status = "done"
    await _fire_all(h)


async def sc_closed_before_deadline_fires(h):
    """То же для дедлайн-пинга."""
    h.specs[ITEM_ID] = Spec(deadline=_now() + timedelta(hours=4))
    await h.flow.restore()
    h.specs[ITEM_ID].status = "done"
    await _fire_all(h)


async def sc_closed_not_restored(h):
    """Закрытая запись при рестарте не оживает ни одним из проходов."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(hours=3), status="done")
    h.specs["8"] = Spec(id="8", reminder=_now() + timedelta(hours=3), status="done")
    h.specs["9"] = Spec(id="9", deadline=_now() + timedelta(hours=4), status="done")
    await h.flow.restore()


async def sc_periodic_skips_missed_recurring(h):
    """periodic re-arm не шлёт «Пропущено» по повторяющимся (иначе дубль живого «🔔»)."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(hours=3), repeat="Ежедневно", repeat_time="10:00")
    await h.flow.restore(periodic=True)


async def sc_restart_shifts_missed_recurring(h):
    """Рестарт: пропущенное повторяющееся сдвигается на будущую дату + «Пропущено … переношу»."""
    h.specs[ITEM_ID] = Spec(reminder=_now() - timedelta(days=2), repeat="Ежедневно", repeat_time="10:00")
    await h.flow.restore()


async def sc_recurring_without_reminder_revived(h):
    """Повтор есть, reminder пуст → оживляется: считается первый future-run и ставится job."""
    h.specs[ITEM_ID] = Spec(repeat="Ежедневно", repeat_time="10:00")
    await h.flow.restore()


async def sc_periodic_rearms_future(h):
    """periodic re-arm заново ставит потерянные jobs (reminder и дедлайн-пинг)."""
    h.specs[ITEM_ID] = Spec(reminder=_now() + timedelta(hours=3))
    h.specs["8"] = Spec(id="8", deadline=_now() + timedelta(hours=5))
    await h.flow.restore(periodic=True)


async def sc_arm_reminder_wins_over_deadline(h):
    """arm(): reminder есть → дедлайн-пинг не ставится (#69); нет → ставится на дедлайн."""
    dl = _wall(_now() + timedelta(hours=6))
    rem = _wall(_now() + timedelta(hours=2))
    await h.flow.arm(ITEM_ID, TITLE, chat_id=TG_IDS[0], tz_offset=TZ, reminder_wall=rem,
                     deadline_wall=dl, recipients=TG_IDS)
    await h.flow.arm("8", TITLE, chat_id=TG_IDS[0], tz_offset=TZ, deadline_wall=dl, recipients=TG_IDS)


def _local(days: int, hh: int, mm: int) -> datetime:
    """Локальное (МСК) время суток через ``days`` дней → aware UTC."""
    d = _now().astimezone(MSK) + timedelta(days=days)
    return d.replace(hour=hh, minute=mm, second=0, microsecond=0).astimezone(timezone.utc)


def _local_day(days: int) -> datetime:
    d = (_now().astimezone(MSK) + timedelta(days=days)).date()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)   # «весь день»: дата


async def sc_restart_shift_keeps_times(h):
    """Рестарт, повтор без «Времени повтора»: дедлайн остаётся 18:30, напоминание — 11:20."""
    h.specs[ITEM_ID] = Spec(reminder=_local(-2, 11, 20), deadline=_local(-2, 18, 30), repeat="Ежедневно")
    await h.flow.restore()


async def sc_restart_shift_keeps_all_day(h):
    """Рестарт: дедлайн «весь день» остаётся днём (не превращается в 03:00)."""
    h.specs[ITEM_ID] = Spec(reminder=_local(-2, 11, 20), deadline=_local_day(-2), all_day=True,
                            repeat="Ежедневно")
    await h.flow.restore()


async def sc_restart_shift_canonical_time_wins(h):
    """Явное «Время повтора» 10:00 возвращает напоминание на 10:00; время дедлайна не трогает."""
    h.specs[ITEM_ID] = Spec(reminder=_local(-2, 11, 20), deadline=_local(-2, 18, 30),
                            repeat="Ежедневно", repeat_time="10:00")
    await h.flow.restore()


async def sc_done_shift_keeps_times(h):
    """«Сделано» по повторяющейся: дедлайн 18:30 и напоминание 11:20 переезжают на новую дату."""
    spec = Spec(reminder=_local(-1, 11, 20), deadline=_local(-1, 18, 30), repeat="Ежедневно")
    h.specs[ITEM_ID] = spec
    await h.complete_recurring(spec)


async def sc_done_shift_keeps_all_day(h):
    """«Сделано»: дедлайн «весь день» остаётся днём."""
    spec = Spec(reminder=_local(-1, 11, 20), deadline=_local_day(-1), all_day=True, repeat="Ежедневно")
    h.specs[ITEM_ID] = spec
    await h.complete_recurring(spec)


async def sc_done_shift_canonical_time_wins(h):
    """«Сделано» с явным «Временем повтора»: напоминание на каноническое 10:00."""
    spec = Spec(reminder=_local(-1, 11, 20), deadline=_local(-1, 18, 30),
                repeat="Ежедневно", repeat_time="10:00")
    h.specs[ITEM_ID] = spec
    await h.complete_recurring(spec)


async def sc_done_shift_without_reminder_starts_from_repeat_time(h):
    """«Сделано», напоминания не было: первый будущий запуск из «Времени повтора»."""
    spec = Spec(deadline=_local(-1, 18, 30), repeat="Ежедневно", repeat_time="10:00")
    h.specs[ITEM_ID] = spec
    await h.complete_recurring(spec)


SCENARIOS = [
    sc_future_reminder, sc_reminder_just_passed, sc_reminder_past_not_scheduled,
    sc_past_after_restart, sc_past_after_restart_not_repeated,
    sc_deadline_without_reminder, sc_deadline_all_day, sc_deadline_fires,
    sc_reminder_fires_and_clears, sc_closed_before_reminder_fires,
    sc_closed_before_deadline_fires, sc_closed_not_restored,
    sc_periodic_skips_missed_recurring, sc_restart_shifts_missed_recurring,
    sc_recurring_without_reminder_revived, sc_periodic_rearms_future,
    sc_arm_reminder_wins_over_deadline,
    sc_restart_shift_keeps_times, sc_restart_shift_keeps_all_day, sc_restart_shift_canonical_time_wins,
    sc_done_shift_keeps_times, sc_done_shift_keeps_all_day, sc_done_shift_canonical_time_wins,
    sc_done_shift_without_reminder_starts_from_repeat_time,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
async def test_nexus_and_arcana_behave_identically(scenario):
    nexus = await _run("nexus", scenario)
    arcana = await _run("arcana", scenario)
    assert nexus == arcana, (
        f"{scenario.__name__}: Nexus и Arcana разошлись — у напоминаний появилась вторая реализация\n"
        f"nexus : {nexus}\narcana: {arcana}"
    )


# ── ожидания: паритет не должен означать «оба неправильно» ──────────────────

async def _both(scenario) -> dict:
    a = await _run("nexus", scenario)
    assert a == await _run("arcana", scenario)
    return a


async def test_future_reminder_is_scheduled_at_the_local_time_and_nothing_is_sent():
    want = _wall(_now() + timedelta(hours=3))
    r = await _both(sc_future_reminder)
    assert r["jobs"] == [("reminder", want)] and r["sent"] == [] and r["cleared"] == []


async def test_just_passed_is_sent_at_once_to_both_chats_and_cleared():
    r = await _both(sc_reminder_just_passed)
    assert r["jobs"] == []
    assert [c for c, _ in r["sent"]] == TG_IDS
    assert all(t.startswith("🔔 <b>Напоминание:</b>") for _, t in r["sent"])
    assert r["cleared"] == [ITEM_ID]


async def test_past_beyond_two_minutes_is_not_scheduled_by_schedule_reminder():
    r = await _both(sc_reminder_past_not_scheduled)
    assert r == {"jobs": [], "sent": [], "cleared": []}


async def test_past_after_restart_is_reported_as_missed_not_lost():
    r = await _both(sc_past_after_restart)
    assert [c for c, _ in r["sent"]] == TG_IDS
    assert all(t.startswith("⏰ <b>Пропущено (") for _, t in r["sent"])
    assert r["cleared"] == [ITEM_ID] and r["jobs"] == []


async def test_missed_is_not_sent_twice():
    r = await _both(sc_past_after_restart_not_repeated)
    assert len(r["sent"]) == len(TG_IDS)


async def test_deadline_without_reminder_is_rearmed_after_restart():
    want = _wall(_now() + timedelta(hours=4))
    r = await _both(sc_deadline_without_reminder)
    assert r["jobs"] == [("deadline", want)] and r["sent"] == []


async def test_all_day_deadline_pings_at_nine_local():
    r = await _both(sc_deadline_all_day)
    assert [(k, w[11:]) for k, w in r["jobs"]] == [("deadline", "09:00")]


async def test_closed_before_firing_stays_silent():
    for sc in (sc_closed_before_reminder_fires, sc_closed_before_deadline_fires):
        r = await _both(sc)
        assert r["sent"] == [] and r["cleared"] == [], sc.__name__


async def test_closed_items_are_never_restored():
    r = await _both(sc_closed_not_restored)
    assert r == {"jobs": [], "sent": [], "cleared": []}


async def test_periodic_does_not_report_missed_recurring():
    r = await _both(sc_periodic_skips_missed_recurring)
    assert r == {"jobs": [], "sent": [], "cleared": []}


async def test_restart_shifts_missed_recurring_and_says_so():
    r = await _both(sc_restart_shifts_missed_recurring)
    assert [c for c, _ in r["sent"]] == TG_IDS
    assert r["jobs"] and r["jobs"][0][0] == "reminder" and r["jobs"][0][1].endswith("T10:00")
    assert r["jobs"][0][1] > _wall(_now())


async def test_deadline_ping_only_when_there_is_no_reminder():
    r = await _both(sc_arm_reminder_wins_over_deadline)
    assert sorted(k for k, _ in r["jobs"]) == ["deadline", "reminder"]


def _today() -> str:
    return _wall(_now())[:10]


@pytest.mark.parametrize("scenario", [sc_restart_shift_keeps_times, sc_done_shift_keeps_times],
                         ids=lambda f: f.__name__)
async def test_shifting_a_series_keeps_the_deadline_and_reminder_time(scenario):
    w = (await _both(scenario))["written"]
    assert w["reminder"].endswith("T11:20"), w
    assert w["deadline"].endswith("T18:30"), w
    assert w["reminder"][:10] > _today() and w["deadline"][:10] > _today(), w


@pytest.mark.parametrize("scenario", [sc_restart_shift_keeps_all_day, sc_done_shift_keeps_all_day],
                         ids=lambda f: f.__name__)
async def test_shifting_a_series_keeps_an_all_day_deadline_a_plain_date(scenario):
    w = (await _both(scenario))["written"]
    assert len(w["deadline"]) == 10 and w["deadline"] > _today(), w
    assert w["reminder"].endswith("T11:20"), w


@pytest.mark.parametrize("scenario", [sc_restart_shift_canonical_time_wins, sc_done_shift_canonical_time_wins],
                         ids=lambda f: f.__name__)
async def test_explicit_repeat_time_is_the_reminder_time_but_never_touches_the_deadline(scenario):
    w = (await _both(scenario))["written"]
    assert w["reminder"].endswith("T10:00"), w
    assert w["deadline"].endswith("T18:30"), w


async def test_done_without_a_reminder_starts_from_the_repeat_time():
    w = (await _both(sc_done_shift_without_reminder_starts_from_repeat_time))["written"]
    assert w["reminder"].endswith("T10:00") and w["reminder"][:10] >= _today(), w
    assert w["deadline"].endswith("T18:30"), w


def test_callback_prefixes_differ_by_design():
    """Единственное намеренное различие: кнопки. Nexus — task_*, Arcana — work_*."""
    from arcana.reminder_adapter import arcana_reminder_flow
    from nexus.handlers import tasks
    cbs = lambda kb: {b.callback_data for row in kb.inline_keyboard for b in row}   # noqa: E731
    n = cbs(tasks._flow.adapter.reminder_kb("1")) | cbs(tasks._flow.adapter.deadline_kb("1"))
    a = cbs(arcana_reminder_flow.adapter.reminder_kb("1")) | cbs(arcana_reminder_flow.adapter.deadline_kb("1"))
    assert all(c.startswith("task_") for c in n) and all(c.startswith("work_") for c in a)


def test_there_is_one_implementation_not_two():
    """Обе обёртки Nexus и Arcana — один и тот же класс движка."""
    from arcana.reminder_adapter import arcana_reminder_flow
    from nexus.handlers import tasks
    from core.reminder_scheduler import ReminderScheduler
    assert type(tasks._flow) is ReminderScheduler is type(arcana_reminder_flow)
