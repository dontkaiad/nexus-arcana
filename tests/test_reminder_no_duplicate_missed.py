"""tests/test_reminder_no_duplicate_missed.py

Regression: одноразовое напоминание, пропущенное пока бот лежал, при частых
рестартах (auto-reload после каждого деплоя) слалось как «⏰ Пропущено»
СНОВА на каждом рестарте — restore-pass2 не обнулял tasks.reminder и
_active_with_past_reminder возвращал задачу опять.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_restore_pass2_oneoff_clears_reminder_after_send():
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.pg_tasks_repo as pgt

    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    task = Task(id="t-9", title="позвонить в банк", repeat="Нет", reminder=past)
    fake_user = {"permissions": {"nexus": True}, "user_id": "u-1"}

    sent_msg = MagicMock()
    sent_msg.chat.id = 7
    sent_msg.message_id = 111
    clear = AsyncMock(return_value=True)

    with patch("core.config.config.allowed_ids", [7]), \
         patch("core.user_manager.get_user", AsyncMock(return_value=fake_user)), \
         patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot") as m_bot, \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks, "save_task_reminder", AsyncMock()), \
         patch.object(pgt.PgTasksRepo, "active_with_future_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_past_reminder", AsyncMock(return_value=[task])), \
         patch.object(pgt.PgTasksRepo, "active_recurring_without_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_future_deadline_no_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "clear_reminder", clear), \
         patch("nexus.repos.tasks_repo.TasksRepo.set_in_progress", AsyncMock()):
        m_bot.send_message = AsyncMock(return_value=sent_msg)
        await tasks.restore_reminders_on_startup()

    m_bot.send_message.assert_awaited_once()
    assert "Пропущено" in m_bot.send_message.await_args.args[1]
    clear.assert_awaited_once_with("t-9")


@pytest.mark.asyncio
async def test_live_reminder_fire_clears_oneoff_reminder():
    """send_reminder (живое срабатывание) обнуляет reminder одноразовой
    задачи — иначе следующий рестарт пришлёт её как «⏰ Пропущено»."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.tasks_repo as trepo

    cur = Task(id="t-5", title="забрать посылку", status="Not started", repeat="Нет")
    clear = AsyncMock(return_value=True)
    sent = MagicMock(); sent.chat.id = 7; sent.message_id = 9

    past = (datetime.now(timezone(timedelta(hours=3))) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M")

    with patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot") as m_bot, \
         patch.object(trepo.TasksRepo, "retrieve_page", AsyncMock(return_value=cur)), \
         patch.object(trepo.TasksRepo, "set_in_progress", AsyncMock()), \
         patch.object(trepo.TasksRepo, "clear_reminder", clear), \
         patch.object(tasks, "save_task_reminder", AsyncMock()):
        m_bot.send_message = AsyncMock(return_value=sent)
        await tasks._schedule_reminder(7, "забрать посылку", past, "t-5", 3)

    m_bot.send_message.assert_awaited_once()
    clear.assert_awaited_once_with("t-5")


@pytest.mark.asyncio
async def test_restore_pass2_recurring_advances_reminder_before_send():
    """Повторяющаяся задача с пропущенным напоминанием: restore-pass2 двигает
    reminder в PG ДО отправки «⏰ Пропущено». Иначе auto-reload между send и
    set_props → следующий рестарт снова видит reminder в прошлом и шлёт дубль
    (#206 для одноразовых, теперь и для повторяющихся)."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.pg_tasks_repo as pgt
    import nexus.repos.tasks_repo as trepo

    past = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    task = Task(id="t-7", title="полить цветы", repeat="Ежедневно",
                repeat_time="09:00", reminder=past)
    fake_user = {"permissions": {"nexus": True}, "user_id": "u-1"}

    calls = []
    sent_msg = MagicMock(); sent_msg.chat.id = 7; sent_msg.message_id = 5

    async def _set_props(_self, _tid, props):
        calls.append(("set_props", props))

    async def _send(*a, **kw):
        calls.append(("send_message", a[1] if len(a) > 1 else kw.get("text", "")))
        return sent_msg

    with patch("core.config.config.allowed_ids", [7]), \
         patch("core.user_manager.get_user", AsyncMock(return_value=fake_user)), \
         patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot") as m_bot, \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks, "save_task_reminder", AsyncMock()), \
         patch.object(pgt.PgTasksRepo, "active_with_future_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_past_reminder", AsyncMock(return_value=[task])), \
         patch.object(pgt.PgTasksRepo, "active_recurring_without_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_future_deadline_no_reminder", AsyncMock(return_value=[])), \
         patch.object(trepo.TasksRepo, "set_props", _set_props):
        m_bot.send_message = _send
        await tasks.restore_reminders_on_startup()

    kinds = [c[0] for c in calls]
    assert kinds == ["set_props", "send_message"], kinds
    assert "Напоминание" in calls[0][1]
    assert "Пропущено" in calls[1][1] and "переношу" in calls[1][1]


@pytest.mark.asyncio
async def test_restore_fans_out_to_all_tgids_of_one_user():
    """Два TG-аккаунта Кай делят один user_id (#202). restore планирует
    задачу ОДИН раз, но fire-time рассылка идёт во все чаты — раньше второй
    проход по tg_id перетирал job (один job_id) и увед уходил лишь в один."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.pg_tasks_repo as pgt
    import nexus.repos.tasks_repo as trepo

    fut = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    task = Task(id="t-1", title="позвонить", repeat="Нет", reminder=fut)

    async def _get_user(tg_id):
        return {"permissions": {"nexus": True}, "user_id": "u-1"}

    added = {}

    def _add_job(fn, **kw):
        added["fn"] = fn
        added["id"] = kw.get("id")

    sched = MagicMock()
    sched.add_job.side_effect = _add_job

    with patch("core.config.config.allowed_ids", [111, 222]), \
         patch("core.user_manager.get_user", _get_user), \
         patch.object(tasks, "_scheduler", sched), \
         patch.object(tasks, "_bot") as m_bot, \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks, "save_task_reminder", AsyncMock()), \
         patch.object(pgt.PgTasksRepo, "active_with_future_reminder", AsyncMock(return_value=[task])), \
         patch.object(pgt.PgTasksRepo, "active_with_past_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_recurring_without_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_future_deadline_no_reminder", AsyncMock(return_value=[])), \
         patch.object(trepo.TasksRepo, "retrieve_page", AsyncMock(return_value=task)), \
         patch.object(trepo.TasksRepo, "set_in_progress", AsyncMock()), \
         patch.object(trepo.TasksRepo, "clear_reminder", AsyncMock()):
        m_bot.send_message = AsyncMock(return_value=MagicMock(chat=MagicMock(id=111), message_id=1))
        await tasks.restore_reminders_on_startup()

        # ровно один job на задачу (не перезатёрт вторым аккаунтом)
        assert sched.add_job.call_count == 1
        assert added["id"] == "reminder_t-1"
        # fire-time — рассылка в оба чата
        await added["fn"]()
        sent_to = {c.args[0] for c in m_bot.send_message.call_args_list}
        assert sent_to == {111, 222}


@pytest.mark.asyncio
async def test_restore_pass4_rearms_deadline_ping(monkeypatch):
    """#212: задача с будущим deadline и без reminder — дедлайн-пинг
    пересобирается при рестарте (раньше нигде не восстанавливался)."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.pg_tasks_repo as pgt

    fut = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    task = Task(id="t-dl", title="сдать отчёт", deadline=fut, reminder="")
    fake_user = {"permissions": {"nexus": True}, "user_id": "u-1"}
    dl_calls = []

    async def _fake_deadline(chat_id, title, dl, task_id, tz_offset=3, recipients=None):
        dl_calls.append((task_id, recipients))

    with patch("core.config.config.allowed_ids", [111, 222]), \
         patch("core.user_manager.get_user", AsyncMock(return_value=fake_user)), \
         patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot", MagicMock()), \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks, "_schedule_deadline_check", _fake_deadline), \
         patch.object(pgt.PgTasksRepo, "active_with_future_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_past_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_recurring_without_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_future_deadline_no_reminder", AsyncMock(return_value=[task])):
        await tasks.restore_reminders_on_startup()

    assert dl_calls == [("t-dl", [111, 222])]


@pytest.mark.asyncio
async def test_periodic_resync_skips_missed_recurring():
    """periodic=True (интервальный re-arm, #210): пропущенное повторяющееся
    напоминание НЕ шлётся как «⏰ Пропущено» — иначе sweep дублировал бы
    живой пинг. Одноразовые — обрабатываются как обычно."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.pg_tasks_repo as pgt
    import nexus.repos.tasks_repo as trepo

    past = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    rec = Task(id="t-r", title="полить цветы", repeat="Ежедневно",
               repeat_time="09:00", reminder=past)
    fake_user = {"permissions": {"nexus": True}, "user_id": "u-1"}
    set_props = AsyncMock()

    with patch("core.config.config.allowed_ids", [7]), \
         patch("core.user_manager.get_user", AsyncMock(return_value=fake_user)), \
         patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot") as m_bot, \
         patch.object(tasks, "_get_user_tz", AsyncMock(return_value=3)), \
         patch.object(tasks, "_schedule_reminder", AsyncMock()), \
         patch.object(tasks, "save_task_reminder", AsyncMock()), \
         patch.object(pgt.PgTasksRepo, "active_with_future_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_past_reminder", AsyncMock(return_value=[rec])), \
         patch.object(pgt.PgTasksRepo, "active_recurring_without_reminder", AsyncMock(return_value=[])), \
         patch.object(pgt.PgTasksRepo, "active_with_future_deadline_no_reminder", AsyncMock(return_value=[])), \
         patch.object(trepo.TasksRepo, "set_props", set_props):
        m_bot.send_message = AsyncMock()
        await tasks.restore_reminders_on_startup(periodic=True)

    m_bot.send_message.assert_not_awaited()
    set_props.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_reminder_fire_keeps_recurring_reminder():
    """Повторяющуюся задачу send_reminder НЕ трогает — её reminder двигает
    cb-обработчик после ответа пользователя."""
    from nexus.handlers import tasks
    from nexus.repos.pg_tasks_repo import Task
    import nexus.repos.tasks_repo as trepo

    cur = Task(id="t-6", title="полить цветы", status="Not started", repeat="Ежедневно")
    clear = AsyncMock()
    sent = MagicMock(); sent.chat.id = 7; sent.message_id = 9
    past = (datetime.now(timezone(timedelta(hours=3))) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M")

    with patch.object(tasks, "_scheduler", MagicMock()), \
         patch.object(tasks, "_bot") as m_bot, \
         patch.object(trepo.TasksRepo, "retrieve_page", AsyncMock(return_value=cur)), \
         patch.object(trepo.TasksRepo, "set_in_progress", AsyncMock()), \
         patch.object(trepo.TasksRepo, "clear_reminder", clear), \
         patch.object(tasks, "save_task_reminder", AsyncMock()):
        m_bot.send_message = AsyncMock(return_value=sent)
        await tasks._schedule_reminder(7, "полить цветы", past, "t-6", 3)

    clear.assert_not_awaited()
