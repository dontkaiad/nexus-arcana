"""tests/test_task_auto_reminder.py — дефолтное напоминание у задач с дедлайном,
мгновенное сохранение, кнопки карточки и перенос дедлайна (+напоминание/jobs/бронь).

Privacy: синтетические названия.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.classifier as clf
from nexus.handlers import tasks as T
from nexus.repos.pg_tasks_repo import Task

TZ = 3
_NOW = lambda: datetime.now(timezone(timedelta(hours=TZ)))  # noqa: E731


def _tomorrow(hm: str = "") -> str:
    d = (_NOW() + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"{d}T{hm}" if hm else d


def _msg():
    m = MagicMock()
    m.from_user = MagicMock(id=777)
    m.chat = MagicMock(id=777)
    m.answer = AsyncMock(return_value=MagicMock(message_id=1))
    return m


async def _parsed(data, text, *, remind_word=False):
    msg = _msg()
    with patch.object(T, "_get_user_tz", AsyncMock(return_value=TZ)), \
         patch.object(T, "_do_save_task", AsyncMock()) as save, \
         patch.object(T, "_show_task_confirm", AsyncMock()) as confirm, \
         patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_has_remind_word", lambda *a, **k: remind_word):
        await T.handle_task_parsed(msg, data, original_text=text)
    return save, confirm, pset


# ── default reminder at creation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timed_deadline_saved_instantly_with_reminder_hour_before():
    data = {"title": "созвон", "deadline": _tomorrow("17:00"), "repeat": "Нет"}
    save, confirm, pset = await _parsed(data, "созвон завтра в 17")
    save.assert_awaited_once()
    assert save.await_args.args[1]["reminder_time"] == _tomorrow("16:00")
    assert save.await_args.args[1]["_auto_reminder"] is True
    confirm.assert_not_awaited()  # без «Всё верно?»
    pset.assert_not_called()


@pytest.mark.asyncio
async def test_all_day_deadline_reminds_at_10():
    data = {"title": "оплатить интернет", "deadline": _tomorrow(), "repeat": "Нет"}
    save, _, _ = await _parsed(data, "оплатить интернет завтра")
    assert save.await_args.args[1]["reminder_time"] == _tomorrow("10:00")


@pytest.mark.asyncio
async def test_explicit_reminder_beats_default():
    data = {"title": "созвон", "deadline": _tomorrow("17:00"),
            "reminder": _tomorrow("15:00"), "repeat": "Нет"}
    save, _, _ = await _parsed(data, "созвон завтра в 17 напомни в 15", remind_word=True)
    saved = save.await_args.args[1]
    assert saved["reminder_time"] == _tomorrow("15:00")
    assert "_auto_reminder" not in saved


@pytest.mark.asyncio
async def test_deadline_in_30_min_gets_no_reminder_but_still_saved():
    soon = (_NOW() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
    data = {"title": "созвон", "deadline": soon, "repeat": "Нет"}
    save, confirm, _ = await _parsed(data, "созвон сегодня в полседьмого")
    save.assert_awaited_once()
    assert not save.await_args.args[1].get("reminder_time")
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_recurring_tasks_untouched():
    data = {"title": "зарядка", "deadline": None, "repeat": "Ежедневно", "repeat_time": "08:00"}
    save, _, _ = await _parsed(data, "зарядка каждый день в 8")
    assert "_auto_reminder" not in save.await_args.args[1]


def test_default_reminder_past_cases():
    yesterday = (_NOW() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert T._default_reminder(yesterday, TZ) is None
    assert T._default_reminder("", TZ) is None
    assert T._default_reminder("мусор", TZ) is None


# ── card buttons ─────────────────────────────────────────────────────────────

def _call(data):
    c = MagicMock()
    c.data = data
    c.from_user = MagicMock(id=777)
    c.answer = AsyncMock()
    c.message = MagicMock(chat=MagicMock(id=777), message_id=5)
    c.message.html_text = "⚡ <b>Задача создана!</b>\n📅 Дедлайн: x\n🔔 Напомню: 2030-01-01 16:00"
    c.message.edit_text = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_noremind_clears_reminder_and_keeps_deadline_ping():
    task = Task(id="9", title="созвон", deadline="2030-01-01T17:00:00+03:00")
    c = _call("task_noremind_9")
    repo = MagicMock(clear_reminder=AsyncMock(), retrieve_page=AsyncMock(return_value=task))
    with patch.object(T, "_repo", repo), patch.object(T, "_get_user_tz", AsyncMock(return_value=TZ)), \
         patch.object(T, "_remove_task_jobs") as rm, \
         patch.object(T, "_schedule_deadline_check", AsyncMock()) as dl:
        await T.task_noremind(c)
    repo.clear_reminder.assert_awaited_once_with("9")
    rm.assert_called_once_with("9")
    dl.assert_awaited_once()
    assert "Без напоминания" in c.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_undo_archives_and_removes_jobs():
    c = _call("task_undo_9")
    repo = MagicMock(set_archived=AsyncMock())
    with patch.object(T, "_repo", repo), patch.object(T, "_remove_task_jobs") as rm:
        await T.task_undo(c)
    repo.set_archived.assert_awaited_once_with("9")
    rm.assert_called_once_with("9")
    assert "Отмена" in c.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_edit_reopens_old_confirm_mode():
    task = Task(id="9", title="созвон", category="💳 Прочее", deadline="2030-01-01T17:00:00+03:00",
                reminder="2030-01-01T16:00:00+03:00")
    c = _call("task_edit_9")
    repo = MagicMock(retrieve_page=AsyncMock(return_value=task), set_archived=AsyncMock())
    with patch.object(T, "_repo", repo), patch.object(T, "_get_user_tz", AsyncMock(return_value=TZ)), \
         patch.object(T, "_remove_task_jobs") as rm, patch.object(T, "_pending_set") as pset, \
         patch.object(T, "_show_task_confirm", AsyncMock()) as confirm:
        await T.task_edit(c)
    repo.set_archived.assert_awaited_once_with("9")
    rm.assert_called_once_with("9")
    pending = pset.call_args.args[1]
    assert pending["deadline"] == "2030-01-01T17:00" and pending["reminder_time"] == "2030-01-01T16:00"
    confirm.assert_awaited_once()


# ── reschedule via edit_record ───────────────────────────────────────────────

async def _edit(task, new_value, *, human=None, booking=None):
    msg = _msg()
    repo = MagicMock(retrieve_page=AsyncMock(return_value=task), set_props=AsyncMock(),
                     clear_reminder=AsyncMock())
    rb = AsyncMock()
    with patch.object(T, "_repo", repo), patch.object(T, "_get_user_tz", AsyncMock(return_value=TZ)), \
         patch.object(T, "_human_date_to_iso", AsyncMock(return_value=human)), \
         patch.object(T, "_remove_task_jobs") as rm, \
         patch.object(T, "_schedule_reminder", AsyncMock()) as sr, \
         patch.object(T, "_schedule_deadline_check", AsyncMock()) as sd, \
         patch("nexus.repos.pg_tasks_repo._ensure_lookups"), \
         patch("core.booking.repo.get_booking_by_link", AsyncMock(return_value=booking)), \
         patch("core.booking.repo.reschedule_booking", rb):
        await T._apply_edit(msg, "task", task.id, task.title, "deadline", new_value)
    return msg, repo, rm, sr, sd, rb


def _task(**kw):
    base = dict(id="9", title="созвон", deadline="2030-01-01T17:00:00+03:00",
                reminder="2030-01-01T16:00:00+03:00")
    base.update(kw)
    return Task(**base)


@pytest.mark.asyncio
async def test_reschedule_to_weekday_keeps_time_and_shifts_reminder_and_jobs():
    msg, repo, rm, sr, sd, rb = await _edit(_task(), "среду", human="2030-01-09")
    props = [c.args[1] for c in repo.set_props.await_args_list]
    assert any("Дедлайн" in p for p in props) and any("Напоминание" in p for p in props)
    assert "2030-01-09T17:00" in str(props[0]["Дедлайн"])          # время старое
    assert "2030-01-09T16:00" in str(props[1]["Напоминание"])      # та же дельта
    rm.assert_called_once_with("9")
    sr.assert_awaited_once()
    assert sr.await_args.args[2] == "2030-01-09T16:00"
    sd.assert_not_awaited()
    rb.assert_not_awaited()  # брони нет
    assert "2030-01-09 17:00" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_reschedule_time_only_keeps_date():
    _, repo, _, sr, _, _ = await _edit(_task(), "18:00")
    assert "2030-01-01T18:00" in str(repo.set_props.await_args_list[0].args[1]["Дедлайн"])
    assert sr.await_args.args[2] == "2030-01-01T17:00"  # reminder +1ч


@pytest.mark.asyncio
async def test_reschedule_without_reminder_schedules_deadline_job():
    _, repo, rm, sr, sd, _ = await _edit(_task(reminder=""), "среду", human="2030-01-09")
    rm.assert_called_once_with("9")
    sr.assert_not_awaited()
    sd.assert_awaited_once()


@pytest.mark.asyncio
async def test_reschedule_moves_linked_booking():
    from core.booking.repo import Booking
    b = Booking(id=5, context="friends", meeting_type_id=None, requester_tg_id=1, requester_name="Рома",
                requester_contact="", start_at=datetime(2030, 1, 1, 14, tzinfo=timezone.utc),
                end_at=datetime(2030, 1, 1, 16, tzinfo=timezone.utc), hours=2.0, status="confirmed",
                note="", source="tg_dm", token="t", nexus_task_id="9", arcana_work_id=None,
                created_at=None, decided_at=None)
    with patch.object(T, "_notify_friend_via_zarya", AsyncMock(return_value=True)):
        msg, _, _, _, _, rb = await _edit(_task(), "среду", human="2030-01-09", booking=b)
    rb.assert_awaited_once()
    bid, start, end = rb.await_args.args
    assert bid == 5
    assert start == datetime(2030, 1, 9, 14, tzinfo=timezone.utc)   # 17:00 МСК
    assert end - start == timedelta(hours=2)                        # длина сохранена
    assert "Бронь тоже перенесена" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_resolve_new_deadline_variants():
    r = T._resolve_new_deadline
    assert await r("18:00", "2030-01-01T17:00", 1, TZ) == "2030-01-01T18:00"
    assert await r("на 18", "2030-01-01T17:00", 1, TZ) == "2030-01-01T18:00"
    assert await r("2030-02-02", "2030-01-01T17:00", 1, TZ) == "2030-02-02"
    with patch.object(T, "_human_date_to_iso", AsyncMock(return_value="2030-01-09")):
        assert await r("среду в 19:30", "2030-01-01T17:00", 1, TZ) == "2030-01-09T19:30"
        assert await r("среду", "2030-01-01", 1, TZ) == "2030-01-09"           # весь день остаётся
        assert await r("15 мая", "2030-01-01T17:00", 1, TZ) == "2030-01-09T17:00"  # «15» — не час


@pytest.mark.asyncio
async def test_weekday_accusative_resolves_without_llm():
    with patch.object(T, "_get_user_tz", AsyncMock(return_value=TZ)), \
         patch.object(T, "ask_claude", AsyncMock(side_effect=AssertionError("LLM не нужен"))):
        assert await T._human_date_to_iso("среду", 1)


# ── classifier ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["перенеси созвон на среду", "сдвинь стоматолога на 18",
                                  "передвинь встречу на завтра"])
def test_move_phrases_hit_edit_prefilter(text):
    assert clf._EDIT_RE.search(text)


def test_edit_prompt_has_move_examples():
    assert "перенеси созвон на среду" in clf._EDIT_PARSE_SYSTEM
    assert "сдвинь стоматолога на 18" in clf._EDIT_PARSE_SYSTEM


# ── уведомление друга через Зарю ────────────────────────────────────────────

def _booking(tg=4242, status="confirmed", note="шашлыки"):
    from core.booking.repo import Booking
    return Booking(id=5, context="friends", meeting_type_id=None, requester_tg_id=tg, requester_name="Рома",
                   requester_contact="", start_at=datetime(2030, 1, 1, 14, tzinfo=timezone.utc),
                   end_at=datetime(2030, 1, 1, 16, tzinfo=timezone.utc), hours=2.0, status=status,
                   note=note, source="tg_dm", token="t", nexus_task_id="9", arcana_work_id=None,
                   created_at=None, decided_at=None)


def _fake_bot(send=None):
    bot = MagicMock()
    bot.send_message = send or AsyncMock()
    bot.session.close = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_move_sends_dm_to_friend_via_zarya_with_cancel_button():
    bot = _fake_bot()
    with patch("core.config.config.booking_bot_token", "zarya-token"), \
         patch("core.config.config.allowed_ids", [1]), \
         patch.object(T, "Bot", MagicMock(return_value=bot)) as bot_cls:
        msg, *_ = await _edit(_task(), "среду", human="2030-01-09", booking=_booking())
    bot_cls.assert_called_once_with(token="zarya-token")
    args, kwargs = bot.send_message.await_args
    assert args[0] == 4242
    assert args[1] == ("⭐ Кай перенесла встречу: вт 01.01 17:00–19:00 → "
                       "ср 09.01 17:00–19:00\n💬 шашлыки")
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "z:cx:5"
    bot.session.close.assert_awaited_once()  # сессия закрыта
    assert "⚠️" not in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_dm_failure_keeps_move_and_warns():
    bot = _fake_bot(AsyncMock(side_effect=RuntimeError("blocked")))
    with patch("core.config.config.booking_bot_token", "zarya-token"), \
         patch("core.config.config.allowed_ids", [1]), \
         patch.object(T, "Bot", MagicMock(return_value=bot)):
        msg, repo, _, _, _, rb = await _edit(_task(), "среду", human="2030-01-09", booking=_booking())
    rb.assert_awaited_once()                 # бронь перенесена
    assert repo.set_props.await_count >= 1   # дедлайн сохранён
    out = msg.answer.await_args.args[0]
    assert "Бронь тоже перенесена" in out and "⚠️ не смогла уведомить Рома" in out
    bot.session.close.assert_awaited_once()  # сессия закрыта и при ошибке


@pytest.mark.asyncio
async def test_no_dm_when_requester_is_owner():
    with patch("core.config.config.booking_bot_token", "zarya-token"), \
         patch("core.config.config.allowed_ids", [4242]), \
         patch.object(T, "Bot", MagicMock()) as bot_cls:
        msg, *_ = await _edit(_task(), "среду", human="2030-01-09", booking=_booking(tg=4242))
    bot_cls.assert_not_called()
    assert "⚠️" not in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cancel_task_cancels_booking_and_notifies_friend():
    bot = _fake_bot()
    sbs = AsyncMock()
    with patch("core.config.config.booking_bot_token", "zarya-token"), \
         patch("core.config.config.allowed_ids", [1]), \
         patch.object(T, "Bot", MagicMock(return_value=bot)), \
         patch("core.booking.repo.get_booking_by_link", AsyncMock(return_value=_booking())), \
         patch("core.booking.repo.set_booking_status", sbs):
        extra = await T._cancel_linked_booking("9")
    sbs.assert_awaited_once_with(5, "cancelled_by_owner")
    assert bot.send_message.await_args.args[0] == 4242
    assert bot.send_message.await_args.args[1].startswith("❌ Кай отменила встречу вт 01.01 17:00–19:00")
    assert "Бронь тоже отменена" in extra and "⚠️" not in extra


@pytest.mark.asyncio
async def test_cancel_task_dm_failure_warns_but_booking_cancelled():
    bot = _fake_bot(AsyncMock(side_effect=RuntimeError("blocked")))
    sbs = AsyncMock()
    with patch("core.config.config.booking_bot_token", "zarya-token"), \
         patch("core.config.config.allowed_ids", [1]), \
         patch.object(T, "Bot", MagicMock(return_value=bot)), \
         patch("core.booking.repo.get_booking_by_link", AsyncMock(return_value=_booking())), \
         patch("core.booking.repo.set_booking_status", sbs):
        extra = await T._cancel_linked_booking("9")
    sbs.assert_awaited_once()
    assert "⚠️ не смогла уведомить Рома" in extra


@pytest.mark.asyncio
async def test_cancel_task_without_booking_is_silent():
    with patch("core.booking.repo.get_booking_by_link", AsyncMock(return_value=None)):
        assert await T._cancel_linked_booking("9") == ""
