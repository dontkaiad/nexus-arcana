"""tests/test_mcp_booking_conflicts.py — предупреждение о пересечении задачи из MCP с бронями друзей (ADR-0039, D2).

Задача при пересечении всё равно создаётся и переносится; брони только читаются. Настоящие строки в таблице booking.
Уникальные строки в имени, контакте и теме брони не должны попасть в ответ API (но имя есть в тексте алерта).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, insert, select

from core import mcp_tasks as rules
from core.booking.tables import booking
from core.db import get_engine
from miniapp.backend.routes import mcp_tasks as route
from tests.test_mcp_write_token import (MARK, OTHER_MARK, W, api, aware, create, day, env, local_now, make_task, resched,  # noqa: F401
                                        rows, snapshot, timed_task)

NAME = "DruzhokUnique17"
CONTACT = "contact-unique-4242@example.test"
NOTE = "TopicUnique999"
TOKEN_PREFIX = "d2test-"


def at(day_offset: int, hh: int, mm: int = 0) -> datetime:
    return aware(day(day_offset), hh, mm)


@pytest.fixture(autouse=True)
def _clean_bookings():
    _wipe()
    yield
    _wipe()


def _wipe():
    with get_engine().begin() as c:
        c.execute(delete(booking).where(booking.c.token.like(TOKEN_PREFIX + "%")))


def make_booking(start: datetime, end: datetime, *, status="confirmed", owner=MARK, name=NAME, contact=CONTACT, note=NOTE) -> int:
    with get_engine().begin() as c:
        return c.execute(insert(booking).values(
            user_id=owner, context="friends", requester_name=name, requester_contact=contact, start_at=start, end_at=end,
            hours=(end - start).total_seconds() / 3600, status=status, note=note, source="web",
            token=TOKEN_PREFIX + secrets.token_hex(6)).returning(booking.c.id)).scalar()


def booking_table() -> list[dict]:
    with get_engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(select(booking).order_by(booking.c.id))]


def new_task(hhmm="10:00", d=3, **body):
    r = create({"title": "Проверка брони", "deadline_date": day(d), **({"deadline_time": hhmm} if hhmm else {}), **body})
    assert r.status_code == 201, r.text
    return r.json()


# ══ границы пересечения (задача 10:00, по правилу busy.py это 10:00–11:00) ═══════════════════════════════════

@pytest.mark.parametrize("b_start,b_end,expected", [
    ((10, 15), (10, 45), True),         # бронь внутри задачи
    ((9, 30), (10, 30), True),          # залезает на начало
    ((10, 30), (12, 0), True),          # залезает на конец
    ((9, 0), (12, 0), True),            # накрывает задачу целиком
    ((10, 0), (11, 0), True),           # совпадает
    ((9, 0), (10, 1), True),            # пересечение в одну минуту с начала
    ((10, 59), (12, 0), True),          # пересечение в одну минуту с конца
    ((9, 0), (10, 0), False),           # конец брони = начало задачи: смежно
    ((11, 0), (12, 0), False),          # начало брони = конец задачи: смежно
    ((8, 0), (9, 59), False),           # раньше с зазором
    ((11, 1), (12, 0), False),          # позже с зазором
])
def test_overlap_boundaries_for_a_timed_task(b_start, b_end, expected):
    make_booking(at(3, *b_start), at(3, *b_end))
    out = new_task("10:00")
    assert bool(out["conflicts"]) is expected, out["conflicts"]
    if expected:
        assert len(out["conflicts"]) == 1 and out["conflicts"][0]["date"] == day(3)


def test_conflict_times_are_reported_in_the_owners_local_time():
    make_booking(at(3, 10, 15), at(3, 10, 45))
    assert new_task("10:00")["conflicts"] == [{"date": day(3), "starts": "10:15", "ends": "10:45"}]


@pytest.mark.parametrize("b_start,b_end,expected", [
    ((3, 0, 30), (3, 1, 0), True),            # ночью того же дня
    ((3, 23, 0), (3, 23, 30), True),          # поздно вечером того же дня
    ((2, 23, 0), (3, 0, 0), False),           # заканчивается ровно в полночь: смежно
    ((3, 23, 30), (4, 0, 30), True),          # начинается в этот день, кончается после полуночи
    ((4, 0, 0), (4, 1, 0), False),            # начинается ровно в полночь следующего дня
])
def test_an_all_day_task_overlaps_any_booking_that_day(b_start, b_end, expected):
    make_booking(at(*b_start), at(*b_end))                               # смещения в днях: даты считаются при запуске, не при сборе
    out = new_task(hhmm=None)                                            # срок без времени = весь день (день 3)
    assert bool(out["conflicts"]) is expected, out["conflicts"]


def test_task_duration_from_duration_min_is_used_on_reschedule():
    tid = timed_task(deadline=aware(day(2), 15, 0), reminder=None, duration_min=120)            # 2 часа
    make_booking(at(5, 11, 30), at(5, 12, 30))
    out = resched(tid, {"deadline_date": day(5), "deadline_time": "10:00"}).json()               # 10:00–12:00 пересекает бронь 11:30
    assert out["conflicts"] == [{"date": day(5), "starts": "11:30", "ends": "12:30"}]
    tid2 = timed_task(deadline=aware(day(2), 15, 0), reminder=None, duration_min=None, title="по умолчанию")
    out2 = resched(tid2, {"deadline_date": day(5), "deadline_time": "10:00"}).json()             # 10:00–11:00 бронь 11:30 не задевает
    assert out2["conflicts"] == []


# ══ какие брони считаются ════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("status,counts", [("confirmed", True), ("pending", True), ("cancelled_by_owner", False),
                                           ("cancelled_by_requester", False), ("completed", False), ("declined", False), ("expired", False)])
def test_only_bookings_that_hold_a_slot_count(status, counts):
    make_booking(at(3, 10, 0), at(3, 11, 0), status=status)
    assert bool(new_task("10:00")["conflicts"]) is counts


def test_a_past_booking_does_not_count_but_one_in_progress_does():
    past_start = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(second=0, microsecond=0)
    make_booking(past_start, past_start + timedelta(hours=1))                                     # уже закончилась
    r = create({"title": "вчера", "deadline_date": day(-1)})
    assert r.status_code == 201 and r.json()["conflicts"] == []
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    make_booking(now - timedelta(minutes=30), now + timedelta(minutes=30))                        # идёт сейчас
    today_local = local_now().date().isoformat()
    r = create({"title": "сегодня на весь день", "deadline_date": today_local})
    assert r.status_code == 201 and len(r.json()["conflicts"]) == 1


def test_another_owners_booking_does_not_count_but_a_legacy_empty_owner_one_does_like_in_busy_py():
    make_booking(at(3, 10, 0), at(3, 11, 0), owner=OTHER_MARK)
    assert new_task("10:00")["conflicts"] == []
    make_booking(at(4, 10, 0), at(4, 11, 0), owner="")                                            # busy.py считает user_id '' общим
    assert len(new_task("10:00", d=4)["conflicts"]) == 1


def test_conflicts_are_sorted_by_start_and_all_are_listed():
    make_booking(at(3, 10, 30), at(3, 12, 0), name="Второй")
    make_booking(at(3, 9, 30), at(3, 10, 15), name="Первый")
    r = create({"title": "две брони", "deadline_date": day(3), "deadline_time": "10:00"})
    got = r.json()["conflicts"]
    assert [c["starts"] for c in got] == ["09:30", "10:30"]


# ══ не блокирует, не меняет брони ════════════════════════════════════════════════════════════════════════════

def test_a_task_is_created_and_moved_even_when_it_overlaps(env):
    make_booking(at(3, 10, 0), at(3, 11, 0))
    out = new_task("10:00")
    assert out["conflicts"] and len(rows()) == 1 and str(rows()[0].id) == out["id"]
    tid = timed_task(deadline=aware(day(1), 15, 0), reminder=None)
    r = resched(tid, {"deadline_date": day(3), "deadline_time": "10:30"})
    assert r.status_code == 200 and r.json()["conflicts"]
    assert snapshot(tid)["deadline"] == aware(day(3), 10, 30)


def test_bookings_are_not_changed_by_create_or_reschedule():
    make_booking(at(3, 10, 0), at(3, 11, 0))
    make_booking(at(3, 10, 30), at(3, 12, 0), status="cancelled_by_owner", name="Отменённая")
    make_booking(at(4, 10, 0), at(4, 11, 0), owner=OTHER_MARK, name="Чужая")
    before = booking_table()
    new_task("10:00")
    tid = timed_task(deadline=aware(day(1), 15, 0), reminder=None)
    assert resched(tid, {"deadline_date": day(3), "deadline_time": "10:30"}).status_code == 200
    assert resched(tid, {"deadline_date": day(8), "deadline_time": "10:30"}).status_code == 200
    assert booking_table() == before                                                              # ни одна колонка ни одной брони


def test_a_failed_booking_lookup_does_not_stop_the_task_and_says_it_could_not_check(monkeypatch, env):
    import core.booking.repo as brepo
    monkeypatch.setattr(brepo, "list_bookings", AsyncMock(side_effect=RuntimeError("db hiccup")))
    r = create({"title": "без проверки", "deadline_date": day(3), "deadline_time": "10:00"})
    assert r.status_code == 201 and r.json()["conflicts"] is None and len(rows()) == 1
    assert len(env.alert.await_args_list) == 1                                                    # только «создал задачу», без предупреждения


# ══ приватность и алерт ══════════════════════════════════════════════════════════════════════════════════════

def test_the_api_answer_has_no_name_contact_note_or_booking_id():
    bid = make_booking(at(3, 10, 0), at(3, 11, 0))
    r = create({"title": "Приватность", "deadline_date": day(3), "deadline_time": "10:00"})
    raw = r.text
    for secret in (NAME, CONTACT, NOTE, "example.test", "friends", "confirmed", TOKEN_PREFIX):
        assert secret not in raw, secret
    [c] = r.json()["conflicts"]
    assert set(c) == {"date", "starts", "ends"}
    assert not any(str(bid) == str(v) for v in c.values())
    tid = timed_task(deadline=aware(day(1), 15, 0), reminder=None)
    raw2 = resched(tid, {"deadline_date": day(3), "deadline_time": "10:00"}).text
    for secret in (NAME, CONTACT, NOTE, "example.test"):
        assert secret not in raw2, secret


def test_the_alert_names_the_friend_but_not_the_contact_or_the_topic(env):
    make_booking(at(3, 10, 0), at(3, 11, 0))
    out = new_task("10:00")
    alerts = [c.args[0] for c in env.alert.await_args_list]
    assert len(alerts) == 2 and alerts[0].startswith("🤖 Claude создал задачу")
    assert alerts[1] == f"⚠️ Задача #{out['id']} <b>Проверка брони</b> пересекается с бронью {NAME} {day(3)} 10:00-11:00, перенеси"
    for text in alerts:
        assert CONTACT not in text and NOTE not in text and "example.test" not in text
    from core.config import config
    assert env.alert.await_args_list[1].args[1] == (config.log_thread_kaimcp or config.log_thread_nexus)         # тот же топик, что у остальных алертов


def test_the_alert_is_html_escaped_and_lists_several_bookings(env):
    make_booking(at(3, 10, 0), at(3, 10, 30), name="Ян <i>&</i>")
    make_booking(at(3, 10, 30), at(3, 11, 0), name="")
    new_task("10:00")
    text = env.alert.await_args_list[-1].args[0]
    assert "Ян &lt;i&gt;&amp;&lt;/i&gt;" in text and "<i>" not in text and "без имени" in text and "бронями:" in text
    assert text.endswith(", перенеси") and text.count(day(3)) == 2


def test_the_reschedule_alert_has_the_task_id_and_title(env):
    make_booking(at(5, 10, 0), at(5, 11, 0))
    tid = timed_task(deadline=aware(day(1), 15, 0), reminder=None, title="Позвонить <b>маме</b>".replace("<b>", "").replace("</b>", ""))
    resched(tid, {"deadline_date": day(5), "deadline_time": "10:00"})
    assert env.alert.await_args_list[-1].args[0] == f"⚠️ Задача #{tid} <b>Позвонить маме</b> пересекается с бронью {NAME} {day(5)} 10:00-11:00, перенеси"


def test_no_warning_alert_when_nothing_overlaps(env):
    make_booking(at(3, 12, 0), at(3, 13, 0))
    new_task("10:00")
    assert len(env.alert.await_args_list) == 1 and "пересекается" not in env.alert.await_args_list[0].args[0]


# ══ чистая логика и защитные свойства ════════════════════════════════════════════════════════════════════════

def _b(status, start, end, name="N"):
    return SimpleNamespace(status=status, start_at=start, end_at=end, requester_name=name)


def test_find_conflicts_is_strict_and_ignores_non_blocking_and_past():
    utc = timezone.utc
    t0 = datetime(2030, 1, 1, 10, 0, tzinfo=utc)
    now = datetime(2029, 12, 31, 0, 0, tzinfo=utc)
    bs = [_b("confirmed", t0 - timedelta(hours=1), t0), _b("confirmed", t0 + timedelta(hours=1), t0 + timedelta(hours=2)),
          _b("cancelled_by_owner", t0, t0 + timedelta(hours=1)), _b("pending", t0 + timedelta(minutes=30), t0 + timedelta(minutes=90), "Pe"),
          _b("confirmed", datetime(2029, 1, 1, tzinfo=utc), datetime(2029, 1, 2, tzinfo=utc))]
    got = rules.find_conflicts(bs, t0, t0 + timedelta(hours=1), now, 3)
    assert [c.name for c in got] == ["Pe"] and got[0].public() == {"date": "2030-01-01", "starts": "13:30", "ends": "14:30"}
    assert "name" not in got[0].public()


def test_the_task_interval_follows_busy_py_one_hour_by_default_and_the_whole_local_day():
    from datetime import date
    s, e = rules.task_interval(date(2030, 1, 1), "10:00", None, 3)
    assert (e - s) == timedelta(minutes=60) and s == datetime(2030, 1, 1, 7, 0, tzinfo=timezone.utc)
    assert rules.task_interval(date(2030, 1, 1), "10:00", 45, 3)[1] - s == timedelta(minutes=45)
    s, e = rules.task_interval(date(2030, 1, 1), None, None, 3)
    assert (e - s) == timedelta(days=1) and s == datetime(2029, 12, 31, 21, 0, tzinfo=timezone.utc)


def test_the_write_token_still_opens_only_the_two_posts_after_this_change():
    from tests.test_mcp_write_token import WRITE_ROUTES, _walk
    walk = _walk(W)
    assert {k for k, (w, wo, _) in walk.items() if w != wo} == WRITE_ROUTES
