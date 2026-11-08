"""tests/test_zarya_trip.py — «уезжаю с X по Y» (Заря): разбор дат, напоминание, блок + задача (реальная БД), отмена, обработчик.

Даты и имена вымышленные. Реальная тестовая БД; строки помечены MARK.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa

from core.booking import trip as T
from core.booking.tables import booking_block
from core.db import get_engine
from nexus.repos.tasks_tables import task_status, tasks

MARK = "test-zarya-trip"
OTHER = "test-zarya-trip-other"
TODAY = date(2026, 11, 8)
MSK = T.MSK


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with get_engine().begin() as c:
        c.execute(tasks.delete().where(tasks.c.user_id.in_([MARK, OTHER])))
        c.execute(booking_block.delete().where(booking_block.c.user_id.in_([MARK, OTHER])))


def D(s):
    y, m, d = map(int, s.split("-"))
    return date(y, m, d)


# ── разбор дат ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,start,end", [
    ("уезжаю с 20 ноября по 5 декабря", "2026-11-20", "2026-12-05"),
    ("Уезжаю С 20 Ноября ПО 5 Декабря!", "2026-11-20", "2026-12-05"),
    ("уезжаю с 20.11 по 05.12", "2026-11-20", "2026-12-05"),
    ("уезжаю с 20.11.2026 по 5.12.2026", "2026-11-20", "2026-12-05"),
    ("уезжаю с 20.11.26 по 5.12.26", "2026-11-20", "2026-12-05"),
    ("уеду 20-25 ноября", "2026-11-20", "2026-11-25"),
    ("уеду 20–25 ноября", "2026-11-20", "2026-11-25"),
    ("улетаю с 20 по 25 ноября", "2026-11-20", "2026-11-25"),
    ("улетаю с 20 до 25 ноября", "2026-11-20", "2026-11-25"),
    ("я уезжаю в отъезд с 3 марта по 9 марта", "2027-03-03", "2027-03-09"),          # год не назван: следующее такое число
    ("уезжаю с 8 ноября по 10 ноября", "2026-11-08", "2026-11-10"),                  # сегодня включительно
    ("уезжаю с 8 ноября по 8 ноября", "2026-11-08", "2026-11-08"),                   # один день
    ("уезжаю с 28 декабря по 5 января", "2026-12-28", "2027-01-05"),                 # через границу года
    ("уезжаю с 28 декабря по 5 января 2027", "2026-12-28", "2027-01-05"),            # год только у конца
    ("уезжаю с 20 по 25 ноября 2027", "2027-11-20", "2027-11-25"),
    ("уезжаю с 1 мая по 5 июня 2027", "2027-05-01", "2027-06-05"),
    ("уезжаю с 7 ноября по 12 ноября", "2027-11-07", "2027-11-12"),                  # 7 ноября уже прошло → следующее
    ("уезжаю с 29 февраля по 3 марта", "2028-02-29", "2028-03-03"),
])
def test_parse_ok(text, start, end):
    t = T.parse_trip(text, TODAY)
    assert t == T.Trip(D(start), D(end))


@pytest.mark.parametrize("text,code", [
    ("уезжаю с 20 по 5 ноября", "reversed"),                       # «по» раньше «с»
    ("уезжаю с 20 ноября по 5 ноября", "reversed"),
    ("уезжаю с 20.12.2026 по 5.12.2026", "reversed"),
    ("уезжаю с 20.12.2025 по 5.1.2026", "past"),                   # явный год в прошлом
    ("уезжаю с 1 ноября 2026 по 3 ноября 2026", "past"),
    ("уезжаю с 20 ноября по 20 июня", "too_long"),
    ("уезжаю с 5 по 20", "month"),                                 # месяц не назван нигде
    ("уезжаю с 31 ноября по 5 декабря", "date"),
    ("уезжаю с 30.02 по 5.03", "date"),
    ("уезжаю с 15.13 по 20.13", "date"),
])
def test_parse_refuses(text, code):
    with pytest.raises(T.TripError) as e:
        T.parse_trip(text, TODAY)
    assert e.value.code == code


@pytest.mark.parametrize("text", [
    "уезжаю завтра в 19", "уезжаю", "привет с 20 по 25 ноября", "завтра в 19 шашлыки с 19 до 23", "", "с 20 по 25 ноября", "когда у Кай окно",
    "уезжаю в пятницу", "отъезд скоро",
])
def test_not_a_trip_phrase(text):
    assert T.parse_trip(text, TODAY) is None and T.is_trip_text(text) is False


def test_span_limits():
    assert T.parse_trip("уезжаю с 20.11 по 18.05", TODAY).days == 180
    with pytest.raises(T.TripError):
        T.parse_trip("уезжаю с 20.11 по 19.05", TODAY)             # 181 день


# ── напоминание ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("days_ahead,offset", [(0, 1), (1, 1), (10, 1), (14, 1), (15, 1), (16, 2), (60, 46)])
def test_reminder_rule(days_ahead, offset):
    r = T.reminder_for(TODAY + timedelta(days=days_ahead), TODAY)
    assert r.date() == TODAY + timedelta(days=offset)
    assert r.hour == 12 and r.minute == 0 and r.utcoffset() == timedelta(hours=3)
    assert r.date() >= TODAY + timedelta(days=1)                    # всегда в будущем, не раньше завтрашнего дня


def test_reminder_far_trip_is_14_days_before():
    start = TODAY + timedelta(days=60)
    assert T.reminder_for(start, TODAY).date() == start - timedelta(days=14)
    assert T.reminder_for(TODAY + timedelta(days=14), TODAY).date() == TODAY + timedelta(days=1)       # ровно 14 → завтра
    assert T.reminder_for(TODAY + timedelta(days=15), TODAY).date() == TODAY + timedelta(days=1)       # 15 → за 14 = завтра
    assert T.reminder_for(TODAY + timedelta(days=16), TODAY).date() == TODAY + timedelta(days=2)


def test_task_title_has_dates_without_year():
    assert T.task_title(T.Trip(D("2026-11-20"), D("2026-12-05"))) == "Выбрать ситтера для котов (20.11–05.12)"


# ── создание: блок + задача (реальная БД) ────────────────────────────────────

def _tasks(user=MARK):
    with get_engine().connect() as c:
        return c.execute(sa.select(tasks.c.id, tasks.c.title, tasks.c.reminder, tasks.c.note, tasks.c.for_sitter, tasks.c.cat_id,
                                   tasks.c.repeat_id, task_status.c.code)
                         .select_from(tasks.join(task_status, task_status.c.id == tasks.c.status_id))
                         .where(tasks.c.user_id == user).order_by(tasks.c.id)).all()


def _blocks(user=MARK):
    with get_engine().connect() as c:
        return c.execute(sa.select(booking_block).where(booking_block.c.user_id == user)).all()


@pytest.mark.asyncio
async def test_create_trip_writes_block_and_choose_sitter_task_with_reminder():
    trip = T.Trip(D("2026-11-20"), D("2026-12-05"))
    res = await T.create_trip(MARK, trip, TODAY)
    assert res.block_created and res.task_created and res.task_id
    (b,) = _blocks()
    assert b.reason == "Отъезд" and b.start_at == datetime(2026, 11, 20, 0, 0, tzinfo=MSK) and b.end_at == datetime(2026, 12, 5, 23, 59, tzinfo=MSK)
    rows = {t.title: t for t in _tasks()}
    pick = rows["Выбрать ситтера для котов (20.11–05.12)"]
    assert pick.code == "Not started" and pick.repeat_id is None and pick.for_sitter is False and pick.cat_id is None
    assert pick.reminder == datetime(2026, 11, 9, 12, 0, tzinfo=MSK) == res.reminder           # завтра 12:00 МСК
    assert pick.reminder.microsecond == 0
    assert "20.11.2026" in pick.note and ("#%d" % b.id) in pick.note
    assert any(t.startswith("🚫") for t in rows)                                                  # обычная задача блока из букинга


@pytest.mark.asyncio
async def test_create_trip_far_departure_reminds_14_days_before():
    trip = T.Trip(TODAY + timedelta(days=60), TODAY + timedelta(days=70))
    res = await T.create_trip(MARK, trip, TODAY)
    assert res.reminder.date() == trip.start - timedelta(days=14) and res.reminder.hour == 12


@pytest.mark.asyncio
async def test_create_trip_is_idempotent_by_title_and_block_dates():
    trip = T.Trip(D("2026-11-20"), D("2026-12-05"))
    a = await T.create_trip(MARK, trip, TODAY)
    b = await T.create_trip(MARK, trip, TODAY)
    assert not b.block_created and not b.task_created and b.block_id == a.block_id and b.task_id == a.task_id
    assert len(_blocks()) == 1 and sum(1 for t in _tasks() if t.title.startswith("Выбрать ситтера")) == 1
    c = await T.create_trip(MARK, T.Trip(D("2026-12-20"), D("2026-12-25")), TODAY)               # другие даты — новые записи
    assert c.block_created and c.task_created and len(_blocks()) == 2


@pytest.mark.asyncio
async def test_done_task_does_not_block_a_new_one_but_isolated_per_user():
    trip = T.Trip(D("2026-11-20"), D("2026-12-05"))
    a = await T.create_trip(MARK, trip, TODAY)
    with get_engine().begin() as c:
        did = c.execute(sa.select(task_status.c.id).where(task_status.c.code == "Done")).scalar()
        c.execute(tasks.update().where(tasks.c.id == a.task_id).values(status_id=did))
    b = await T.create_trip(MARK, trip, TODAY)
    assert b.task_created and b.task_id != a.task_id                                              # выполненная не считается «уже есть»
    o = await T.create_trip(OTHER, trip, TODAY)
    assert o.block_created and o.task_created
    assert all(t.title.startswith(("Выбрать", "🚫")) for t in _tasks(OTHER)) and len(_blocks(OTHER)) == 1


@pytest.mark.asyncio
async def test_cancel_trip_removes_block_and_archives_both_tasks():
    res = await T.create_trip(MARK, T.Trip(D("2026-11-20"), D("2026-12-05")), TODAY)
    assert await T.cancel_trip(MARK, res.block_id, res.task_id) == (True, True)
    assert _blocks() == [] and all(t.code == "Archived" for t in _tasks())
    assert await T.cancel_trip(MARK, res.block_id, res.task_id) == (False, False)                 # повтор безвреден


@pytest.mark.asyncio
async def test_cancel_never_touches_done_foreign_or_other_tasks():
    res = await T.create_trip(MARK, T.Trip(D("2026-11-20"), D("2026-12-05")), TODAY)
    other = await T.create_trip(OTHER, T.Trip(D("2026-11-20"), D("2026-12-05")), TODAY)
    # чужие id: ни блок, ни задача чужого пользователя не трогаются
    assert await T.cancel_trip(MARK, other.block_id, other.task_id) == (False, False)
    assert len(_blocks(OTHER)) == 1 and all(t.code == "Not started" for t in _tasks(OTHER) if t.title.startswith("Выбрать"))
    # выполненную задачу (ситтер уже выбран) отмена не архивирует
    with get_engine().begin() as c:
        did = c.execute(sa.select(task_status.c.id).where(task_status.c.code == "Done")).scalar()
        c.execute(tasks.update().where(tasks.c.id == res.task_id).values(status_id=did))
    assert await T.cancel_trip(MARK, res.block_id, res.task_id) == (True, False)
    assert {t.code for t in _tasks() if t.title.startswith("Выбрать")} == {"Done"}
    # посторонняя задача (не «Выбрать ситтера…») тоже не архивируется по подделанному id
    with get_engine().begin() as c:
        sid = c.execute(sa.select(task_status.c.id).where(task_status.c.code == "Not started")).scalar()
        stray = c.execute(tasks.insert().values(title="Купить молоко", status_id=sid, user_id=MARK).returning(tasks.c.id)).scalar()
    assert await T.cancel_trip(MARK, 987654321, stray) == (False, False)
    assert {t.code for t in _tasks() if t.title == "Купить молоко"} == {"Not started"}


@pytest.mark.asyncio
async def test_block_in_the_database_closes_the_slots():
    from core.booking.busy import busy_intervals
    await T.create_trip(MARK, T.Trip(D("2026-11-20"), D("2026-11-22")), TODAY)
    ivs = await busy_intervals(MARK, datetime(2026, 11, 19, tzinfo=MSK), datetime(2026, 11, 24, tzinfo=MSK))
    assert any(i.source == "block" and i.start <= datetime(2026, 11, 20, 12, tzinfo=MSK) <= i.end for i in ivs)


# ── обработчик ────────────────────────────────────────────────────────────────

def _msg(text, tg_id=42):
    m = MagicMock()
    m.text = text
    m.from_user = SimpleNamespace(id=tg_id, full_name="Кай")
    m.chat = SimpleNamespace(type="private", id=tg_id)
    m.bot = SimpleNamespace()
    m.answer = AsyncMock()
    return m


def test_filter_admin_only_and_needs_verb_and_range():
    from zarya.handlers import _is_trip
    assert _is_trip(_msg("уезжаю с 20 по 25 ноября"), "admin") is True
    assert _is_trip(_msg("уезжаю с 20 по 25 ноября"), "friend") is False
    assert _is_trip(_msg("уезжаю с 20 по 25 ноября"), "guest") is False
    assert _is_trip(_msg("/slots уезжаю с 20 по 25 ноября"), "admin") is False
    assert _is_trip(_msg("уезжаю завтра в 19"), "admin") is False
    assert _is_trip(_msg("завтра в 19 шашлыки"), "admin") is False


@pytest.mark.asyncio
async def test_handler_creates_and_confirms_with_cancel_button():
    from zarya.handlers import on_trip
    m = _msg("уезжаю с 20.11 по 05.12")
    res = T.TripResult(block_id=7, block_created=True, task_id=9, task_created=True, reminder=datetime(2026, 11, 9, 12, tzinfo=MSK))
    with patch("zarya.handlers._owner_user_id", AsyncMock(return_value="uid")), \
         patch("zarya.handlers.trip_mod.create_trip", AsyncMock(return_value=res)) as ct, \
         patch("zarya.handlers._bot_addressed", return_value=True):
        await on_trip(m, role="admin")
    trip = ct.await_args.args[1]
    assert trip.start.month == 11 and trip.start.day == 20 and trip.end.month == 12 and trip.end.day == 5
    text = m.answer.await_args.args[0]
    assert "20.11–05.12" in text and "16 дн." in text and "09.11 в 12:00 МСК" in text and "дублирую" not in text
    btn = m.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert btn.text == "❌ Отменить" and btn.callback_data == "z:trip_cx:7:9"


@pytest.mark.asyncio
async def test_handler_says_when_nothing_new_was_written():
    from zarya.handlers import on_trip
    m = _msg("уезжаю с 20.11 по 05.12")
    res = T.TripResult(7, False, 9, False, datetime(2026, 11, 9, 12, tzinfo=MSK))
    with patch("zarya.handlers._owner_user_id", AsyncMock(return_value="uid")), \
         patch("zarya.handlers.trip_mod.create_trip", AsyncMock(return_value=res)), patch("zarya.handlers._bot_addressed", return_value=True):
        await on_trip(m, role="admin")
    assert "ничего не дублирую" in m.answer.await_args.args[0]


@pytest.mark.parametrize("text,fragment", [
    ("уезжаю с 20 по 5 ноября", "«по» раньше «с»"),
    ("уезжаю с 20.12.2025 по 5.1.2026", "уже прошли"),
    ("уезжаю с 20 ноября по 20 июня", "полугода"),
    ("уезжаю с 31 ноября по 5 декабря", "Такой даты нет"),
])
@pytest.mark.asyncio
async def test_handler_refuses_bad_dates_without_writing(text, fragment):
    from zarya.handlers import on_trip
    m = _msg(text)
    with patch("zarya.handlers.trip_mod.create_trip", AsyncMock()) as ct, patch("zarya.handlers._bot_addressed", return_value=True), \
         patch("zarya.handlers.datetime") as dt:
        dt.now.return_value = datetime(2026, 11, 8, 10, 0, tzinfo=MSK)
        await on_trip(m, role="admin")
    ct.assert_not_awaited()
    assert fragment in m.answer.await_args.args[0]


def _call(data, role="admin"):
    c = MagicMock()
    c.data = data
    c.answer = AsyncMock()
    c.message = MagicMock()
    c.message.edit_text = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_cancel_callback_is_admin_only_and_validates_data():
    from zarya.handlers import on_trip_cancel
    with patch("zarya.handlers._owner_user_id", AsyncMock(return_value="uid")), \
         patch("zarya.handlers.trip_mod.cancel_trip", AsyncMock(return_value=(True, True))) as ct:
        c = _call("z:trip_cx:7:9")
        await on_trip_cancel(c, role="friend")
        ct.assert_not_awaited()
        for bad in ("z:trip_cx:", "z:trip_cx:x:9", "z:trip_cx:7", "z:trip_cx:-1:9", "z:trip_cx:7:-2", "z:trip_cx:7:9:1", "z:trip_cx:0:9"):
            await on_trip_cancel(_call(bad), role="admin")
        ct.assert_not_awaited()
        await on_trip_cancel(_call("z:trip_cx:7:9"), role="admin")
        ct.assert_awaited_once_with("uid", 7, 9)
        await on_trip_cancel(_call("z:trip_cx:7:0"), role="admin")
        assert ct.await_args.args == ("uid", 7, None)


@pytest.mark.asyncio
async def test_cancel_callback_second_press_says_already_cancelled():
    from zarya.handlers import on_trip_cancel
    c = _call("z:trip_cx:7:9")
    with patch("zarya.handlers._owner_user_id", AsyncMock(return_value="uid")), \
         patch("zarya.handlers.trip_mod.cancel_trip", AsyncMock(return_value=(False, False))):
        await on_trip_cancel(c, role="admin")
    assert "Уже отменено" in c.message.edit_text.await_args.args[0]


def test_trip_router_is_registered_before_quick_booking():
    from zarya import handlers as H
    names = [getattr(h.callback, "__name__", "") for h in H.router.message.handlers]
    assert names.index("on_trip") < names.index("on_quick_booking") < names.index("nl_slots")


@pytest.mark.asyncio
async def test_cancel_trip_never_deletes_a_block_that_is_not_a_trip():
    from core.booking.repo import add_block
    vacation = await add_block(MARK, datetime(2026, 12, 1, tzinfo=MSK), datetime(2026, 12, 3, tzinfo=MSK), "Свой отпуск")
    assert await T.cancel_trip(MARK, int(vacation["id"]), None) == (False, False)           # id чужого по смыслу блока (не «Отъезд»)
    assert len(_blocks()) == 1
