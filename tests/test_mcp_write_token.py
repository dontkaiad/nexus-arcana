"""tests/test_mcp_write_token.py — запись для kai-mcp: POST /api/mcp/tasks и POST /api/mcp/tasks/{id}/reschedule (ADR-0039).

Путь авторизации настоящий (current_user_id не подменяется), база настоящая (строки в таблице tasks), токены и tg_id
вымышленные. Подменены только то, что наружу: планировщик напоминаний и отправка алерта в Telegram.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select

from core import mcp_tasks as rules
from core.config import config
from core.db import get_engine
from core.repos.cats_tables import cats as cats_table
from miniapp.backend import (care_period_auth, care_write_auth, cats_read_auth, cats_write_auth, mcp_read_auth,
                             mcp_write_auth as gate, sitter_lists_auth)
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes import mcp_tasks as route
from miniapp.backend.routes import tasks as tasks_route
from miniapp.backend.tg_auth import issue_session
from nexus.repos import pg_tasks_repo
from nexus.repos.tasks_tables import task_category, task_priority, task_repeat, task_status, tasks as tasks_table

TOKEN_W = "test-only-mcp-write-" + "d4" * 10                      # вымышленный, 40 символов
TOKEN_R = "test-only-mcp-read-" + "e5" * 10
OWNER = 700000321                                                # владелец токена записи (вымышленный tg_id)
OTHER = 700000001                                                # другой разрешённый пользователь (обычный вход)
MARK, OTHER_MARK = "test-mcp-write-user", "test-mcp-write-other-user"
PUBLIC_PEER = ("8.8.8.8", 5000)
W = {"Authorization": f"Bearer {TOKEN_W}"}
R = {"Authorization": f"Bearer {TOKEN_R}"}
SECRET = "test-session-secret-for-mcp-write-tests"
REAL_SCHEDULE = route._schedule                                  # настоящая функция до подмены фикстурой


def local_now() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None, second=0, microsecond=0)


def day(offset: int = 0) -> str:
    return (local_now().date() + timedelta(days=offset)).isoformat()


def when(offset_days: int, hh: int = 10, mm: int = 0) -> str:
    return f"{day(offset_days)}T{hh:02d}:{mm:02d}"


class Calls:
    def __init__(self) -> None:
        self.schedule = AsyncMock()
        self.alert = AsyncMock(return_value=True)
        self.user_ids: list[int] = []


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "mcp_write_token", TOKEN_W)
    monkeypatch.setattr(config, "mcp_write_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "mcp_read_token", TOKEN_R)
    monkeypatch.setattr(config, "mcp_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER, OTHER])
    monkeypatch.setattr(config, "session_secret", SECRET)
    for mod in (gate, mcp_read_auth, cats_read_auth, sitter_lists_auth, care_write_auth, care_period_auth, cats_write_auth):
        monkeypatch.setattr(mod, "_limiter", cats_read_auth.FailureLimiter())
    monkeypatch.setattr(route, "_create_limiter", rules.DailyLimiter(rules.CREATE_LIMIT_PER_DAY))
    monkeypatch.setattr(route, "_reschedule_limiter", rules.DailyLimiter(rules.RESCHEDULE_LIMIT_PER_DAY))
    calls = Calls()

    async def user_id(tg_id):
        calls.user_ids.append(tg_id)
        return MARK if tg_id == OWNER else OTHER_MARK

    async def tz(tg_id):
        return local_now().date(), 3

    monkeypatch.setattr(route, "get_user_id", user_id)
    monkeypatch.setattr(route, "today_user_tz", tz)
    monkeypatch.setattr(route, "_schedule", calls.schedule)
    monkeypatch.setattr(route, "notify_log_group", calls.alert)
    app.dependency_overrides.pop(current_user_id, None)
    _cleanup()
    yield calls
    _cleanup()
    app.dependency_overrides.pop(current_user_id, None)


def _cleanup():
    with get_engine().begin() as c:
        c.execute(delete(tasks_table).where(tasks_table.c.user_id.in_([MARK, OTHER_MARK])))
        c.execute(delete(cats_table).where(cats_table.c.user_id == MARK))


def api(peer=PUBLIC_PEER, **kw):
    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": peer}
        await app(scope, receive, send)
    return TestClient(with_peer, **kw)


def create(body=None, headers=W, **kw):
    base = {"title": "Купить корм", "deadline_date": day(3)}
    return api().post("/api/mcp/tasks", json={**base, **(body or {})}, headers=headers, **kw)


def resched(task_id, body=None, headers=W):
    base = {"deadline_date": day(5)}
    return api().post(f"/api/mcp/tasks/{task_id}/reschedule", json={**base, **(body or {})}, headers=headers)


def rows(owner=MARK):
    with get_engine().connect() as c:
        return c.execute(select(tasks_table).where(tasks_table.c.user_id == owner).order_by(tasks_table.c.id)).fetchall()


def lookup_id(table, code):
    with get_engine().connect() as c:
        return c.execute(select(table.c.id).where(table.c.code == code)).scalar()


def make_task(*, title="Позвонить маме", status="Not started", deadline=None, all_day=False, reminder=None, repeat=None,
              for_sitter=False, cat_id=None, owner=MARK, note="заметка владельца", duration_min=45, prio="🟡 Важно",
              category="💳 Прочее") -> str:
    pg_tasks_repo._ensure_lookups()
    with get_engine().begin() as c:
        row = c.execute(insert(tasks_table).values(
            title=title, status_id=lookup_id(task_status, status), priority_id=lookup_id(task_priority, prio),
            category_id=lookup_id(task_category, category), deadline=deadline, deadline_all_day=all_day, reminder=reminder,
            repeat_id=lookup_id(task_repeat, repeat) if repeat else None, for_sitter=for_sitter, cat_id=cat_id, user_id=owner,
            note=note, duration_min=duration_min, shared=False).returning(tasks_table.c.id)).first()
    return str(row[0])


def aware(date_str: str, hh=10, mm=0) -> datetime:
    y, m, d = (int(x) for x in date_str.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=3)))


def snapshot(task_id: str) -> dict:
    with get_engine().connect() as c:
        r = c.execute(select(tasks_table).where(tasks_table.c.id == int(task_id))).fetchone()
    return dict(r._mapping)


MOVABLE = {"deadline", "deadline_all_day", "reminder", "updated_at"}


# ══ шлюз ═════════════════════════════════════════════════════════════════════

def test_valid_bearer_creates_a_task_as_the_configured_owner(env):
    r = create()
    assert r.status_code == 201
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert env.user_ids == [OWNER]                                              # владелец всегда MCP_WRITE_OWNER_TG_ID
    [row] = rows()
    assert row.title == "Купить корм" and str(row.id) == r.json()["id"]


@pytest.mark.parametrize("header", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer   "},
                                    {"Authorization": "bearer " + TOKEN_W[:-1]}, {"Authorization": "Bearer " + TOKEN_W + "x"},
                                    {"Authorization": "tma initdata"}, {"Authorization": "Basic " + TOKEN_W},
                                    {"Authorization": "Token " + TOKEN_W}, {"Authorization": TOKEN_W}])
def test_missing_or_bad_bearer_is_401_and_nothing_is_written(header):
    for r in (create(headers=header), resched("1", headers=header)):
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert rows() == []


def test_scheme_is_case_insensitive():
    assert create(headers={"Authorization": f"bearer {TOKEN_W}"}).status_code == 201


def test_normal_login_does_not_open_the_write_routes_at_all():
    from tests.test_mcp_read_token import init_data
    hdr = {"X-Telegram-Init-Data": init_data(OTHER)}
    cookie, _ = issue_session(OTHER, secret=SECRET, remember=False)
    assert create(headers=hdr).status_code == 401
    assert api().post("/api/mcp/tasks", json={"title": "x", "deadline_date": day(1)}, cookies={"hl_session": cookie}).status_code == 401
    assert create(headers={**W, **hdr}).status_code == 201                       # токен записи сам по себе работает, initData не мешает
    assert resched("1", headers=hdr).status_code == 401


def test_rate_limit_is_429_after_ten_bad_attempts_even_for_the_valid_token(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=lambda: t["now"]))
    c = api()
    bad = {"Authorization": "Bearer wrong"}
    body = {"title": "x", "deadline_date": day(2)}
    assert [c.post("/api/mcp/tasks", json=body, headers=bad).status_code for _ in range(10)] == [401] * 10
    r = c.post("/api/mcp/tasks", json=body, headers=bad)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60
    assert c.post("/api/mcp/tasks", json=body, headers=W).status_code == 429       # верному токену тоже, пока окно не освободилось
    assert c.post("/api/mcp/tasks/1/reschedule", json={"deadline_date": day(2)}, headers=W).status_code == 429
    t["now"] += 61
    assert c.post("/api/mcp/tasks", json=body, headers=W).status_code == 201


def test_valid_requests_are_never_counted_as_failures():
    c = api()
    assert all(c.post("/api/mcp/tasks", json={"title": f"t{i}", "deadline_date": day(2)}, headers=W).status_code == 201 for i in range(15))


@pytest.mark.parametrize("break_it", ["token_empty", "token_short", "owner_zero", "owner_not_allowed", "equals_read", "equals_cats",
                                      "equals_sitter", "equals_care_write", "equals_care_period", "equals_cats_write", "equals_booking"])
def test_gate_is_off_when_misconfigured_any_bearer_is_401(monkeypatch, break_it):
    if break_it == "token_empty":
        monkeypatch.setattr(config, "mcp_write_token", "")
    elif break_it == "token_short":
        monkeypatch.setattr(config, "mcp_write_token", "x" * 31)
    elif break_it == "owner_zero":
        monkeypatch.setattr(config, "mcp_write_owner_tg_id", 0)
    elif break_it == "owner_not_allowed":
        monkeypatch.setattr(config, "allowed_ids", [OTHER])
    else:
        attr = {"equals_read": "mcp_read_token", "equals_cats": "cats_read_token", "equals_sitter": "sitter_lists_token",
                "equals_care_write": "care_write_token", "equals_care_period": "care_period_token",
                "equals_cats_write": "cats_write_token", "equals_booking": "booking_service_token"}[break_it]
        monkeypatch.setattr(config, attr, TOKEN_W)
    for sent in (TOKEN_W, "", "wrong"):
        assert create(headers={"Authorization": f"Bearer {sent}"}).status_code == 401
    assert rows() == []


def test_read_token_does_not_open_write_and_write_token_does_not_open_read():
    # токен чтения → запись закрыта
    assert create(headers=R).status_code == 401 and resched("1", headers=R).status_code == 401
    assert rows() == []
    # токен записи → оба читающих маршрута закрыты
    assert api().get("/api/tasks", headers=W).status_code == 401
    assert api().get("/api/arcana/reminders", headers=W).status_code == 401


def test_the_read_gate_is_off_if_its_token_equals_the_write_token(monkeypatch):
    monkeypatch.setattr(config, "mcp_read_token", TOKEN_W)                         # одинаковые токены: выключены оба направления
    assert api().get("/api/tasks", headers=W).status_code == 401
    assert create(headers=W).status_code == 401


# ══ создание ═════════════════════════════════════════════════════════════════

def test_create_writes_exactly_what_was_asked_and_nothing_else(env):
    r = create({"title": "  Записаться к врачу  ", "deadline_date": day(4), "deadline_time": "14:30", "reminder": when(4, 13, 0),
                "prio": "🔴", "category": "Здоровье"})
    assert r.status_code == 201
    [row] = rows()
    assert row.title == "Записаться к врачу"
    assert row.deadline == aware(day(4), 14, 30) and row.deadline_all_day is False
    assert row.reminder == aware(day(4), 13, 0)
    assert row.user_id == MARK
    assert row.for_sitter is False and row.repeat_id is None and row.day_of_week_id is None and row.repeat_time is None
    assert row.cat_id is None and row.note is None and row.completed_at is None and row.parent_task_id is None
    assert row.shared is False and row.duration_min is None and row.care_label is None
    assert row.status_id == lookup_id(task_status, "Not started")
    assert row.priority_id == lookup_id(task_priority, "🔴 Срочно")
    assert row.category_id == lookup_id(task_category, "🏥 Здоровье")


def test_create_defaults_category_to_other_prio_to_important_and_all_day_deadline():
    r = create({"title": "Просто дело"})
    [row] = rows()
    assert row.deadline_all_day is True and row.reminder is None
    assert row.category_id == lookup_id(task_category, "💳 Прочее") and row.priority_id == lookup_id(task_priority, "🟡 Важно")
    assert r.json()["category"] == "💳 Прочее" and r.json()["prio"] == "🟡" and r.json()["deadline_time"] is None


@pytest.mark.parametrize("prio,code", [("🔴", "🔴 Срочно"), ("🟡", "🟡 Важно"), ("⚪", "⚪️ Можно потом"), ("⚪️", "⚪️ Можно потом")])
def test_create_priorities_from_the_existing_emoji_set(prio, code):
    assert create({"prio": prio}).status_code == 201
    assert rows()[0].priority_id == lookup_id(task_priority, code)


@pytest.mark.parametrize("prio", ["🟢", "high", "", "🔴🔴", "срочно"])
def test_create_unknown_priority_is_422(prio):
    assert create({"prio": prio}).status_code == 422 and rows() == []


@pytest.mark.parametrize("cat", ["Практика", "🔮 Практика", "практика", "Расходники", "🕯️ Расходники", "🕯 Расходники", "РАСХОДНИКИ"])
def test_create_forbidden_categories_are_422_with_a_clear_code(cat):
    r = create({"category": cat})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "forbidden_category" and rows() == []


@pytest.mark.parametrize("cat", ["Выдуманная", "", "💼 Фриланс"])
def test_create_unknown_category_is_422(cat):
    assert create({"category": cat}).status_code == 422 and rows() == []


@pytest.mark.parametrize("cat", ["Прочее", "💳 Прочее", "коты", "🐾 Коты", "Хобби/Учеба"])
def test_create_known_categories_by_name_or_full_code(cat):
    assert create({"category": cat}).status_code == 201


def reset_lookups():
    """_ensure_lookups перечитывает справочники, только если пуст словарь статусов: сбрасываем все разом."""
    for name in ("_status_id", "_status_code", "_repeat_id", "_repeat_code", "_dow_id", "_dow_code", "_priority_id",
                 "_priority_code", "_category_id", "_category_code"):
        getattr(pg_tasks_repo, name).clear()


def test_create_forbidden_category_stays_forbidden_even_if_the_dictionary_gets_such_a_row():
    with get_engine().begin() as c:
        c.execute(insert(task_category).values(code="🔮 Практика"))
    try:
        reset_lookups()
        r = create({"category": "Практика"})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "forbidden_category" and rows() == []
    finally:
        with get_engine().begin() as c:
            c.execute(delete(task_category).where(task_category.c.code == "🔮 Практика"))
        reset_lookups()


@pytest.mark.parametrize("title,ok", [("а", True), ("x" * 200, True), ("x" * 201, False), ("", False), ("   ", False),
                                      ("две\nстроки", False), ("таб\tтут", False), ("нул\x00ь", False), ("<b>жирно</b>", False),
                                      ("<a href='https://evil'>клик</a>", False), ("a > b", False), ("Письмо Ивану &co", True)])
def test_create_title_rules(title, ok):
    r = create({"title": title})
    assert (r.status_code == 201) is ok, r.text
    assert len(rows()) == (1 if ok else 0)


@pytest.mark.parametrize("extra", [{"note": "тайное"}, {"for_sitter": True}, {"repeat": "Ежедневно"}, {"status": "Done"},
                                   {"user_id": "someone"}, {"cat_id": 1}, {"duration_min": 30}, {"owner": 123}])
def test_create_rejects_any_field_beyond_the_allowed_ones(extra):
    assert create(extra).status_code == 422 and rows() == []


@pytest.mark.parametrize("deadline_date,ok", [(-1, True), (-2, False), (0, True), (730, True), (731, False), (900, False)])
def test_create_deadline_window_yesterday_to_two_years(deadline_date, ok):
    today = local_now().date()
    d = (rules.add_years(today, 2) if deadline_date == 730 else
         rules.add_years(today, 2) + timedelta(days=1) if deadline_date == 731 else today + timedelta(days=deadline_date))
    r = create({"deadline_date": d.isoformat()})
    assert (r.status_code == 201) is ok, r.text
    if not ok:
        assert r.json()["detail"]["code"] == "date_out_of_window"


@pytest.mark.parametrize("bad", ["2026-02-30", "13.11.2026", "2026-11-1", "завтра", "", "2026-11-13T10:00", "1700000000"])
def test_create_bad_date_formats_are_422(bad):
    assert create({"deadline_date": bad}).status_code == 422 and rows() == []


@pytest.mark.parametrize("bad", ["25:00", "9:30", "12:60", "12-30", "noon", "", "24:00"])
def test_create_bad_time_is_422(bad):
    assert create({"deadline_time": bad}).status_code == 422 and rows() == []


def test_create_deadline_time_boundaries():
    for hhmm in ("00:00", "23:59"):
        assert create({"deadline_time": hhmm, "title": f"t {hhmm}"}).status_code == 201


def test_create_reminder_rules():
    assert create({"deadline_date": day(3), "deadline_time": "12:00", "reminder": when(3, 12, 1)}).status_code == 422     # позже срока
    r = create({"deadline_date": day(3), "deadline_time": "12:00", "reminder": when(3, 12, 1)})
    assert r.json()["detail"]["code"] == "reminder_after_deadline"
    assert create({"deadline_date": day(3), "deadline_time": "12:00", "reminder": when(3, 12, 0), "title": "в срок"}).status_code == 201
    assert create({"deadline_date": day(3), "reminder": when(3, 23, 30), "title": "весь день"}).status_code == 201      # срок на весь день
    assert create({"deadline_date": day(3), "reminder": when(4, 0, 0)}).status_code == 422                             # следующий день
    past = (local_now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    r = create({"deadline_date": day(0), "deadline_time": "23:59", "reminder": past})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "reminder_in_the_past"
    for bad in ("2026-11-13 10:00", "2026-11-13T10:00:00", "завтра в 10", "2026-11-13T25:00"):
        assert create({"reminder": bad}).status_code == 422


def test_create_schedules_like_the_bot_reminder_if_given_else_a_deadline_ping(env):
    create({"title": "с напоминанием", "deadline_date": day(3), "deadline_time": "15:00", "reminder": when(3, 14, 0)})
    kw = env.schedule.await_args.kwargs
    assert kw["reminder_local"] == when(3, 14, 0) and env.schedule.await_args.args[1] == "с напоминанием"
    create({"title": "без напоминания", "deadline_date": day(3), "deadline_time": "15:00"})
    assert env.schedule.await_args.kwargs["reminder_local"] is None and env.schedule.await_args.kwargs["deadline_local"] == f"{day(3)}T15:00"
    create({"title": "на весь день", "deadline_date": day(3)})
    assert env.schedule.await_args.kwargs["deadline_local"] == f"{day(3)}T09:00"          # как в боте: 09:00 по умолчанию


def test_scheduler_wrapper_follows_the_bot_and_never_raises(monkeypatch):
    import nexus.handlers.tasks as bot
    seen = []

    async def rem(*a, **k):
        seen.append(("reminder", a[1], a[2]))

    async def dl(*a, **k):
        seen.append(("deadline", a[1], a[2]))

    monkeypatch.setattr(bot, "_remove_task_jobs", lambda tid: seen.append(("remove", tid)))
    monkeypatch.setattr(bot, "_schedule_reminder", rem)
    monkeypatch.setattr(bot, "_schedule_deadline_check", dl)
    asyncio.run(REAL_SCHEDULE(1, "t", "77", 3, reminder_local="2026-12-01T10:00", deadline_local="2026-12-01T12:00"))
    asyncio.run(REAL_SCHEDULE(1, "t", "77", 3, reminder_local=None, deadline_local="2026-12-01T12:00"))
    assert seen == [("remove", "77"), ("reminder", "t", "2026-12-01T10:00"), ("remove", "77"), ("deadline", "t", "2026-12-01T12:00")]

    async def boom(*a, **k):
        raise RuntimeError("scheduler down")
    monkeypatch.setattr(bot, "_schedule_reminder", boom)
    asyncio.run(REAL_SCHEDULE(1, "t", "77", 3, reminder_local="2026-12-01T10:00", deadline_local=None))        # не бросает


def test_create_limit_20_per_day_and_failed_requests_do_not_count(monkeypatch):
    t = {"now": 0.0}
    monkeypatch.setattr(route, "_create_limiter", rules.DailyLimiter(20, clock=lambda: t["now"]))
    for i in range(5):
        assert create({"title": ""}).status_code == 422                          # отказы лимит не едят
    for i in range(20):
        assert create({"title": f"t{i}"}).status_code == 201, i
    r = create({"title": "двадцать первая"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit" and int(r.headers["retry-after"]) > 0
    assert len(rows()) == 20
    t["now"] += 86400 + 1
    assert create({"title": "на следующие сутки"}).status_code == 201


# ══ перенос ══════════════════════════════════════════════════════════════════

def timed_task(**kw):
    kw.setdefault("deadline", aware(day(2), 15, 0))
    kw.setdefault("reminder", aware(day(2), 14, 0))
    return make_task(**kw)


def test_reschedule_moves_only_the_deadline_and_the_reminder(env):
    tid = timed_task()
    before = snapshot(tid)
    r = resched(tid, {"deadline_date": day(5), "deadline_time": "16:30", "reminder": when(5, 15, 0)})
    assert r.status_code == 200
    after = snapshot(tid)
    assert {k: v for k, v in before.items() if k not in MOVABLE} == {k: v for k, v in after.items() if k not in MOVABLE}
    assert after["deadline"] == aware(day(5), 16, 30) and after["reminder"] == aware(day(5), 15, 0) and after["deadline_all_day"] is False
    assert after["note"] == "заметка владельца" and after["title"] == "Позвонить маме" and after["duration_min"] == 45
    assert after["status_id"] == before["status_id"] and after["for_sitter"] is False


def test_reschedule_answer_has_old_and_new_values():
    tid = timed_task()
    body = resched(tid, {"deadline_date": day(5), "deadline_time": "16:30", "reminder": when(5, 15, 0)}).json()
    assert set(body) == {"id", "title", "old", "new"} and body["id"] == tid and body["title"] == "Позвонить маме"
    assert body["old"] == {"deadline": day(2), "deadline_time": "15:00", "reminder": when(2, 14, 0)}
    assert body["new"] == {"deadline": day(5), "deadline_time": "16:30", "reminder": when(5, 15, 0)}


def test_reschedule_keeps_the_old_time_of_day_and_an_all_day_deadline_stays_all_day():
    tid = timed_task(reminder=None)
    body = resched(tid, {"deadline_date": day(6)}).json()
    assert body["new"]["deadline_time"] == "15:00" and snapshot(tid)["deadline"] == aware(day(6), 15, 0)
    tid2 = make_task(deadline=aware(day(2), 3, 0), all_day=True, title="весь день")
    body = resched(tid2, {"deadline_date": day(7)}).json()
    assert body["old"]["deadline_time"] is None and body["new"]["deadline_time"] is None
    assert snapshot(tid2)["deadline_all_day"] is True


def test_reschedule_without_reminder_leaves_the_old_one_if_it_is_still_valid_and_rejects_it_if_not():
    tid = timed_task(deadline=aware(day(4), 15, 0), reminder=aware(day(4), 14, 0))
    assert resched(tid, {"deadline_date": day(8)}).status_code == 200            # прежнее напоминание не позже нового срока
    assert snapshot(tid)["reminder"] == aware(day(4), 14, 0)
    r = resched(tid, {"deadline_date": day(3)})                                  # новый срок раньше прежнего напоминания
    assert r.status_code == 422 and r.json()["detail"]["code"] == "reminder_after_deadline"
    assert snapshot(tid)["deadline"] == aware(day(8), 15, 0)                     # ничего не поменялось
    assert resched(tid, {"deadline_date": day(3), "reminder": when(3, 9, 0)}).status_code == 200


def test_reschedule_reminder_after_deadline_or_in_the_past_is_rejected():
    tid = timed_task()
    r = resched(tid, {"deadline_date": day(5), "deadline_time": "10:00", "reminder": when(5, 10, 1)})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "reminder_after_deadline"
    past = (local_now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    r = resched(tid, {"deadline_date": day(0), "deadline_time": "23:59", "reminder": past})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "reminder_in_the_past"


@pytest.mark.parametrize("offset,ok", [(-1, True), (-2, False), (0, True)])
def test_reschedule_date_window_lower_edge(offset, ok):
    tid = timed_task(reminder=None)
    assert (resched(tid, {"deadline_date": day(offset)}).status_code == 200) is ok


def test_reschedule_date_window_upper_edge():
    tid = timed_task(reminder=None)
    top = rules.add_years(local_now().date(), 2)
    assert resched(tid, {"deadline_date": top.isoformat()}).status_code == 200
    r = resched(tid, {"deadline_date": (top + timedelta(days=1)).isoformat()})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "date_out_of_window"


def test_reschedule_overdue_and_in_progress_tasks_can_be_moved():
    overdue = make_task(deadline=aware(day(-3), 10, 0), title="просрочена")
    assert resched(overdue, {"deadline_date": day(2)}).status_code == 200
    wip = make_task(status="In progress", deadline=aware(day(1), 10, 0), title="в работе")
    assert resched(wip, {"deadline_date": day(3)}).status_code == 200


@pytest.mark.parametrize("kind,code", [("done", "task_closed"), ("archived", "task_closed"), ("sitter", "sitter_task"),
                                       ("recurring", "recurring_task"), ("cat", "cat_task"), ("booking", "booking_task")])
def test_reschedule_refuses_what_it_must_not_touch(monkeypatch, kind, code):
    kw = {"done": {"status": "Done"}, "archived": {"status": "Archived"}, "sitter": {"for_sitter": True},
          "recurring": {"repeat": "Еженедельно"}, "cat": {}, "booking": {}}[kind]
    if kind == "cat":
        from core.repos import pg_cats_repo as crepo
        kw = {"cat_id": crepo.create(MARK, "Тучка", [])}
    tid = timed_task(**kw)
    if kind == "booking":
        import core.booking.repo as brepo
        monkeypatch.setattr(brepo, "get_booking_by_link", AsyncMock(return_value=object()))
    before = snapshot(tid)
    r = resched(tid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == code
    assert snapshot(tid) == before                                               # ни одна колонка не тронута


def test_reschedule_somebody_elses_or_missing_task_is_404_without_a_hint():
    foreign = make_task(owner=OTHER_MARK, deadline=aware(day(2), 10, 0), title="чужая")
    before = snapshot(foreign)
    r1, r2 = resched(foreign), resched("999999999")
    assert r1.status_code == r2.status_code == 404 and r1.json() == r2.json()
    assert snapshot(foreign) == before


def test_reschedule_task_with_an_empty_owner_is_not_the_owners():
    tid = make_task(owner="", deadline=aware(day(2), 10, 0), title="ничья")
    try:
        assert resched(tid).status_code == 404
    finally:
        with get_engine().begin() as c:
            c.execute(delete(tasks_table).where(tasks_table.c.id == int(tid)))


@pytest.mark.parametrize("bad_id", ["abc", "1.5", "-1", "1 2", "x" * 30, "0x10"])
def test_reschedule_malformed_id_is_422(bad_id):
    assert resched(bad_id).status_code == 422


@pytest.mark.parametrize("extra", [{"title": "Новое имя"}, {"note": "новая заметка"}, {"status": "Done"}, {"prio": "🔴"},
                                   {"category": "Коты"}, {"for_sitter": True}, {"repeat": "Ежедневно"}, {"delete": True},
                                   {"archived": True}, {"duration_min": 10}, {"user_id": "x"}])
def test_reschedule_cannot_change_anything_but_the_three_fields(extra):
    tid = timed_task()
    before = snapshot(tid)
    assert resched(tid, extra).status_code == 422
    assert snapshot(tid) == before


def test_there_is_no_way_to_delete_or_edit_through_the_write_routes():
    tid = timed_task()
    c = api()
    for method, path in (("DELETE", f"/api/mcp/tasks/{tid}"), ("PATCH", f"/api/mcp/tasks/{tid}"), ("PUT", f"/api/mcp/tasks/{tid}"),
                         ("GET", f"/api/mcp/tasks/{tid}"), ("GET", "/api/mcp/tasks"), ("POST", f"/api/mcp/tasks/{tid}/cancel"),
                         ("POST", f"/api/mcp/tasks/{tid}/done"), ("POST", f"/api/mcp/tasks/{tid}/edit")):
        assert c.request(method, path, headers=W, json={}).status_code in (401, 404, 405), (method, path)
    # существующие записывающие маршруты по токену записи закрыты
    for path in (f"/api/tasks/{tid}/done", f"/api/tasks/{tid}/cancel", f"/api/tasks/{tid}/edit", f"/api/tasks/{tid}/postpone"):
        assert c.post(path, json={"title": "хак", "date": day(9)}, headers=W).status_code == 401, path
    assert c.post("/api/tasks", json={"title": "через старый маршрут"}, headers=W).status_code == 401
    assert snapshot(tid)["title"] == "Позвонить маме" and len(rows()) == 1


def test_reschedule_reschedules_the_scheduler_like_the_bot(env):
    tid = timed_task()
    resched(tid, {"deadline_date": day(5), "deadline_time": "16:00", "reminder": when(5, 15, 0)})
    kw = env.schedule.await_args.kwargs
    assert kw["reminder_local"] == when(5, 15, 0) and kw["deadline_local"] == f"{day(5)}T16:00" and kw["duration_min"] == 45
    tid2 = timed_task(reminder=None, title="без напоминания")
    resched(tid2, {"deadline_date": day(6), "deadline_time": "11:00"})
    assert env.schedule.await_args.kwargs["reminder_local"] is None and env.schedule.await_args.kwargs["deadline_local"] == f"{day(6)}T11:00"


def test_reschedule_limit_30_per_day_and_refusals_do_not_count(monkeypatch):
    t = {"now": 0.0}
    monkeypatch.setattr(route, "_reschedule_limiter", rules.DailyLimiter(30, clock=lambda: t["now"]))
    tid = timed_task(reminder=None)
    done = make_task(status="Done", deadline=aware(day(2), 9, 0), title="закрыта")
    for _ in range(5):
        assert resched(done).status_code == 409                                  # отказы лимит не едят
    for i in range(30):
        assert resched(tid, {"deadline_date": day(2 + i % 5)}).status_code == 200, i
    r = resched(tid)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit"
    t["now"] += 86400 + 1
    assert resched(tid).status_code == 200


# ══ приватность: ответы и алерты ═════════════════════════════════════════════

def test_create_answer_has_only_the_expected_fields_and_no_secrets():
    r = create({"title": "Лишнего нет", "reminder": when(3, 9, 0), "deadline_time": "10:00"})
    body = r.json()
    assert set(body) == {"id", "title", "deadline", "deadline_time", "reminder", "prio", "category"}
    blob = r.text
    for secret in (TOKEN_W, TOKEN_R, MARK, str(OWNER), "user_id", "for_sitter", "note"):
        assert secret not in blob, secret


def test_alerts_say_what_happened_and_hold_nothing_extra(env):
    tid = timed_task()
    create({"title": "Алерт <тест>".replace("<", "").replace(">", ""), "deadline_date": day(3), "deadline_time": "10:00"})
    resched(tid, {"deadline_date": day(5), "deadline_time": "16:30"})
    created, moved = (c.args[0] for c in env.alert.await_args_list)
    assert created == f"🤖 Claude создал задачу: <b>Алерт тест</b>, срок {day(3)} 10:00"
    assert moved == f"🤖 Claude перенёс задачу #{tid} <b>Позвонить маме</b>: было {day(2)} 15:00, стало {day(5)} 16:30"
    for text in (created, moved):
        for secret in (TOKEN_W, TOKEN_R, MARK, str(OWNER), "Bearer", "заметка владельца", "user_id"):
            assert secret not in text
    assert all(c.args[1] == (config.log_thread_kaimcp or config.log_thread_nexus) for c in env.alert.await_args_list)


def test_alert_goes_to_the_kaimcp_topic_when_configured(monkeypatch, env):
    monkeypatch.setattr(config, "log_thread_kaimcp", "2219")
    create()
    assert env.alert.await_args.args[1] == "2219"


def test_alert_text_is_escaped_for_telegram_html():
    assert rules.alert_created("A&B", "2026-11-14", None) == "🤖 Claude создал задачу: <b>A&amp;B</b>, срок 2026-11-14"
    t = rules.alert_rescheduled("7", "x", ("2026-11-14", None), (None, None))
    assert t.endswith("было 2026-11-14, стало без срока")


def test_alert_failure_does_not_break_the_request(monkeypatch, env):
    env.alert.side_effect = RuntimeError("telegram down")
    r = create()
    assert r.status_code == 201 and len(rows()) == 1


def test_a_task_nobody_owns_is_not_created(monkeypatch):
    async def nobody(tg_id):
        return None
    monkeypatch.setattr(route, "get_user_id", nobody)
    r = create()
    assert r.status_code == 409 and r.json()["detail"]["code"] == "owner_unknown" and rows() == []


# ══ существующие маршруты не изменились ══════════════════════════════════════

def test_mini_app_login_on_existing_routes_is_unchanged(monkeypatch):
    from tests.test_mcp_read_token import FakeTasksRepo, init_data
    monkeypatch.setattr(tasks_route, "_tasks_repo", FakeTasksRepo())
    monkeypatch.setattr(tasks_route, "today_user_tz", AsyncMock(return_value=(local_now().date(), 3)))
    monkeypatch.setattr(tasks_route, "get_user_id", AsyncMock(side_effect=lambda tg: f"user-{tg}"))
    c = api()
    assert c.get("/api/tasks").status_code == 401
    r = c.get("/api/tasks", params={"filter": "all"}, headers={"X-Telegram-Init-Data": init_data(OTHER)})
    assert r.status_code == 200 and r.json()["tasks"]
    cookie, _ = issue_session(OTHER, secret=SECRET, remember=False)
    assert api().get("/api/tasks", params={"filter": "all"}, cookies={"hl_session": cookie}).status_code == 200
    assert api().get("/api/tasks", params={"filter": "all"},
                     headers={"Authorization": "tma x", "X-Telegram-Init-Data": init_data(OTHER)}).status_code == 200


# ══ главный тест: обход ВСЕХ маршрутов ═══════════════════════════════════════

WRITE_ROUTES = {("POST", "/api/mcp/tasks"), ("POST", "/api/mcp/tasks/{task_id}/reschedule")}
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _gated_routes() -> list[tuple[str, str]]:
    def has_gate(dependant) -> bool:
        return any(d.call is gate.mcp_write_gate or has_gate(d) for d in dependant.dependencies)
    return sorted((m, r.path) for r in app.routes if isinstance(r, APIRoute) and has_gate(r.dependant) for m in sorted(r.methods))


def test_the_write_gate_is_attached_to_exactly_the_two_post_routes():
    assert _gated_routes() == sorted(WRITE_ROUTES)


def _walk(headers) -> dict:
    paths: dict[str, set[str]] = {}
    for r in app.routes:
        p, ms = getattr(r, "path", None), getattr(r, "methods", None)
        if p:
            paths.setdefault(p, set()).update(ms or [])
    c = api(raise_server_exceptions=False)
    norm = lambda code: 401 if code == 429 else code                           # noqa: E731
    out = {}
    import re
    for path, declared in sorted(paths.items()):
        url = re.sub(r"\{[^}]*\}", "1", path)
        for method in sorted((set(declared) | set(ALL_METHODS)) - {"HEAD", "OPTIONS"}):
            kw = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            with_t = c.request(method, url, headers=headers, **kw).status_code
            without = c.request(method, url, **kw).status_code
            out[(method, path)] = (norm(with_t), norm(without), with_t)
    return out


def test_write_token_opens_exactly_the_two_new_posts_across_all_routes():
    """ГЛАВНЫЙ тест: все маршруты приложения (app.routes) всеми методами с токеном записи."""
    walk = _walk(W)
    assert len(walk) > 500
    for prefix in ("/api/finance", "/api/arcana", "/api/cats", "/api/cat-health", "/api/care", "/api/memory", "/api/people",
                   "/api/booking", "/api/lists", "/api/tasks", "/api/mcp"):
        assert any(p.startswith(prefix) for _, p in walk), prefix
    opened = {k for k, (w, wo, _) in walk.items() if w != wo}                  # токен изменил исход запроса
    assert opened == WRITE_ROUTES, sorted(opened ^ WRITE_ROUTES)
    bad = [f"{m} {p} -> {raw}" for (m, p), (w, wo, raw) in walk.items()
           if (m, p) not in WRITE_ROUTES and w not in (401, 403, 404, 405, 422) and w != wo]
    assert not bad, "\n".join(bad)                                              # публичные маршруты (200 и без токена) исключены равенством
    assert all(raw < 500 for _, _, raw in walk.values())
    # оба читающих GET, которые открывает токен чтения, по токену записи закрыты
    for key in (("GET", "/api/tasks"), ("GET", "/api/arcana/reminders")):
        assert walk[key][0] == 401, key


def test_read_token_opens_neither_of_the_write_posts_and_nothing_new():
    walk = _walk(R)
    assert walk[("POST", "/api/mcp/tasks")][0] == 401 and walk[("POST", "/api/mcp/tasks/{task_id}/reschedule")][0] == 401
    opened = {k for k, (w, wo, _) in walk.items() if w != wo}
    assert opened == {("GET", "/api/tasks"), ("GET", "/api/arcana/reminders")}, sorted(opened)
