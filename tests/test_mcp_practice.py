"""tests/test_mcp_practice.py — создание личной практики для kai-mcp: POST /api/mcp/practice (ADR-0040).

Путь авторизации настоящий (current_user_id не подменяется), база настоящая (строки в таблице works, занятость Зари читается
настоящими busy_intervals и day_agenda), токены, tg_id и имена вымышленные. Подменены только то, что наружу: отправка алерта в
Telegram и список клиентов (имена клиентов в тестах придуманы).
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import secrets
import textwrap
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import delete, insert, select

from arcana.repos.clients_repo import Client
from arcana.repos.pg_works_repo import PgWorksRepo
from arcana.repos.works_tables import work_priority, work_status, works as works_table
from core import mcp_practice as rules
from core.booking.agenda import day_agenda
from core.booking.busy import busy_intervals
from core.booking.tables import booking
from core.config import config
from core.db import get_engine
from miniapp.backend import (care_period_auth, care_write_auth, cats_read_auth, cats_write_auth, mcp_practice_auth as gate,
                             mcp_read_auth, mcp_write_auth, sitter_lists_auth)
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes import mcp_practice as route
from miniapp.backend.tg_auth import issue_session
from tests.test_mcp_write_token import PUBLIC_PEER, api, aware, day, local_now  # noqa: F401

TOKEN_P = "test-only-mcp-practice-" + "a7" * 10                 # вымышленный, 43 символа
TOKEN_W = "test-only-mcp-write-" + "d4" * 10
TOKEN_R = "test-only-mcp-read-" + "e5" * 10
OWNER = 700000321                                                # владелец токена практики (вымышленный tg_id)
OTHER = 700000001
MARK, OTHER_MARK = "test-mcp-practice-user", "test-mcp-practice-other-user"
P = {"Authorization": f"Bearer {TOKEN_P}"}
SECRET = "test-session-secret-for-mcp-practice-tests"
CATEGORIES = ["🃏 Расклад", "✨ Ритуал", "📱 Соцсети", "🛒 Расходники", "📚 Обучение", "🗂️ Прочее"]
SLOT_CATEGORIES = {"🃏 Расклад", "✨ Ритуал"}
BOOKING_PREFIX = "practice-test-"
CLIENTS = [("Мария Петрова", "paid"), ("Олег", "free"), ("Игорь", "paid"), ("Вера", "paid"), ("Ян", "paid"), ("Тестовая Кай", "self")]


class FakeClients:
    def __init__(self, items=CLIENTS):
        self.items = list(items)
        self.calls = 0

    async def list_all(self, user_id=""):
        self.calls += 1
        return [Client(id=str(i + 1), name=n, contact="", request="", notes="", since="", type_code=t) for i, (n, t) in enumerate(self.items)]


class Calls:
    def __init__(self) -> None:
        self.alert = AsyncMock(return_value=True)
        self.reminder = AsyncMock()
        self.deadline_ping = AsyncMock()
        self.notify_user = AsyncMock()
        self.clients = FakeClients()
        self.user_ids: list = []


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "mcp_practice_token", TOKEN_P)
    monkeypatch.setattr(config, "mcp_practice_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "mcp_write_token", TOKEN_W)
    monkeypatch.setattr(config, "mcp_write_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "mcp_read_token", TOKEN_R)
    monkeypatch.setattr(config, "mcp_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER, OTHER])
    monkeypatch.setattr(config, "session_secret", SECRET)
    for mod in (gate, mcp_read_auth, mcp_write_auth, cats_read_auth, sitter_lists_auth, care_write_auth, care_period_auth, cats_write_auth):
        monkeypatch.setattr(mod, "_limiter", cats_read_auth.FailureLimiter())
    monkeypatch.setattr(route, "_create_limiter", rules.DailyLimiter(rules.CREATE_LIMIT_PER_DAY))
    calls = Calls()

    async def user_id(tg_id):
        calls.user_ids.append(tg_id)
        return MARK if tg_id == OWNER else OTHER_MARK

    async def tz(tg_id):
        return local_now().date(), 3

    monkeypatch.setattr(route, "get_user_id", user_id)
    monkeypatch.setattr(route, "today_user_tz", tz)
    monkeypatch.setattr(route, "notify_log_group", calls.alert)
    monkeypatch.setattr(route, "_clients_repo", calls.clients)
    # то, чего практика делать не должна: напоминание, дедлайн-пинг, сообщение в чат
    from core.reminder_scheduler import ReminderScheduler
    from miniapp.backend.routes import writes
    monkeypatch.setattr(ReminderScheduler, "schedule_reminder", calls.reminder)
    monkeypatch.setattr(ReminderScheduler, "schedule_deadline_check", calls.deadline_ping)
    monkeypatch.setattr(writes, "notify_user", calls.notify_user)
    import core.bot_notify as bot_notify
    monkeypatch.setattr(bot_notify, "notify_user", calls.notify_user)
    app.dependency_overrides.pop(current_user_id, None)
    _cleanup()
    yield calls
    _cleanup()
    app.dependency_overrides.pop(current_user_id, None)


def _cleanup():
    with get_engine().begin() as c:
        c.execute(delete(works_table).where(works_table.c.user_id.in_([MARK, OTHER_MARK])))
        c.execute(delete(booking).where(booking.c.token.like(BOOKING_PREFIX + "%")))


def create(body=None, headers=P, **kw):
    base = {"title": "Почистить колоду", "deadline_date": day(3)}
    return api().post("/api/mcp/practice", json={**base, **(body or {})}, headers=headers, **kw)


def rows(owner=MARK):
    with get_engine().connect() as c:
        return c.execute(select(works_table).where(works_table.c.user_id == owner).order_by(works_table.c.id)).fetchall()


def code_of(table, id_):
    with get_engine().connect() as c:
        return c.execute(select(table.c.code).where(table.c.id == id_)).scalar()


def make_booking(start: datetime, end: datetime, *, status="confirmed", name="DruzhokUnique17") -> int:
    with get_engine().begin() as c:
        return c.execute(insert(booking).values(
            user_id=MARK, context="friends", requester_name=name, requester_contact="contact-unique-4242@example.test",
            start_at=start, end_at=end, hours=(end - start).total_seconds() / 3600, status=status, note="TopicUnique999",
            source="web", token=BOOKING_PREFIX + secrets.token_hex(6)).returning(booking.c.id)).scalar()


def zarya_state() -> dict:
    """Всё, что строит занятость Зари: busy_intervals (слоты, календарь, ICS-лента) и day_agenda (то, что показывает Заря)."""
    start = datetime.now(timezone.utc) - timedelta(days=2)
    end = datetime.now(timezone.utc) + timedelta(days=30)
    busy = asyncio.run(busy_intervals(MARK, start, end))
    agenda = asyncio.run(day_agenda(MARK, (start + timedelta(days=1)).date(), end.date()))
    return {"busy": [(b.start, b.end, b.source, b.all_day) for b in busy],
            "agenda": [(a.day, a.kind, a.start, a.end, a.all_day) for a in agenda]}


# ══ шлюз ═════════════════════════════════════════════════════════════════════

def test_valid_bearer_creates_a_practice_as_the_configured_owner(env):
    r = create()
    assert r.status_code == 201
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert env.user_ids == [OWNER]
    [row] = rows()
    assert row.title == "Почистить колоду" and str(row.id) == r.json()["id"]


@pytest.mark.parametrize("header", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer   "},
                                    {"Authorization": "bearer " + TOKEN_P[:-1]}, {"Authorization": "Bearer " + TOKEN_P + "x"},
                                    {"Authorization": "tma initdata"}, {"Authorization": "Basic " + TOKEN_P},
                                    {"Authorization": "Token " + TOKEN_P}, {"Authorization": TOKEN_P}])
def test_missing_or_bad_bearer_is_401_and_nothing_is_written(header):
    r = create(headers=header)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert rows() == []


def test_scheme_is_case_insensitive():
    assert create(headers={"Authorization": f"bearer {TOKEN_P}"}).status_code == 201


def test_normal_login_does_not_open_the_practice_route_at_all():
    from tests.test_mcp_read_token import init_data
    hdr = {"X-Telegram-Init-Data": init_data(OTHER)}
    cookie, _ = issue_session(OTHER, secret=SECRET, remember=False)
    assert create(headers=hdr).status_code == 401
    assert api().post("/api/mcp/practice", json={"title": "x", "deadline_date": day(1)}, cookies={"hl_session": cookie}).status_code == 401
    assert create(headers={**P, **hdr}).status_code == 201                       # токен практики сам по себе работает, initData не мешает
    assert rows(OTHER_MARK) == []


def test_rate_limit_is_429_after_ten_bad_attempts_even_for_the_valid_token(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=lambda: t["now"]))
    c = api()
    bad = {"Authorization": "Bearer wrong"}
    body = {"title": "x", "deadline_date": day(2)}
    assert [c.post("/api/mcp/practice", json=body, headers=bad).status_code for _ in range(10)] == [401] * 10
    r = c.post("/api/mcp/practice", json=body, headers=bad)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60
    assert c.post("/api/mcp/practice", json=body, headers=P).status_code == 429     # верному токену тоже, пока окно не освободилось
    t["now"] += 61
    assert c.post("/api/mcp/practice", json=body, headers=P).status_code == 201


def test_valid_requests_are_never_counted_as_failures():
    c = api()
    assert all(c.post("/api/mcp/practice", json={"title": f"t{i}", "deadline_date": day(2)}, headers=P).status_code == 201 for i in range(15))


OTHER_TOKENS = {"equals_read": "mcp_read_token", "equals_write": "mcp_write_token", "equals_cats": "cats_read_token",
                "equals_sitter": "sitter_lists_token", "equals_care_write": "care_write_token", "equals_care_period": "care_period_token",
                "equals_cats_write": "cats_write_token", "equals_booking": "booking_service_token"}


@pytest.mark.parametrize("break_it", ["token_empty", "token_short", "owner_zero", "owner_not_allowed", *OTHER_TOKENS])
def test_gate_is_off_when_misconfigured_any_bearer_is_401(monkeypatch, break_it):
    if break_it == "token_empty":
        monkeypatch.setattr(config, "mcp_practice_token", "")
    elif break_it == "token_short":
        monkeypatch.setattr(config, "mcp_practice_token", "x" * 31)
    elif break_it == "owner_zero":
        monkeypatch.setattr(config, "mcp_practice_owner_tg_id", 0)
    elif break_it == "owner_not_allowed":
        monkeypatch.setattr(config, "allowed_ids", [OTHER])
    else:
        monkeypatch.setattr(config, OTHER_TOKENS[break_it], TOKEN_P)
    for sent in (TOKEN_P, "", "wrong"):
        assert create(headers={"Authorization": f"Bearer {sent}"}).status_code == 401
    assert rows() == []


def test_read_and_write_tokens_do_not_open_practice_and_practice_opens_neither_of_them():
    assert create(headers={"Authorization": f"Bearer {TOKEN_R}"}).status_code == 401
    assert create(headers={"Authorization": f"Bearer {TOKEN_W}"}).status_code == 401
    assert rows() == []
    assert api().get("/api/tasks", headers=P).status_code == 401
    assert api().get("/api/arcana/reminders", headers=P).status_code == 401
    assert api().post("/api/mcp/tasks", json={"title": "x", "deadline_date": day(2)}, headers=P).status_code == 401
    assert api().post("/api/mcp/tasks/1/reschedule", json={"deadline_date": day(2)}, headers=P).status_code == 401


def test_the_read_and_write_gates_are_off_if_their_token_equals_the_practice_token(monkeypatch):
    monkeypatch.setattr(config, "mcp_read_token", TOKEN_P)
    monkeypatch.setattr(config, "mcp_write_token", TOKEN_P)
    assert api().get("/api/tasks", headers=P).status_code == 401
    assert api().post("/api/mcp/tasks", json={"title": "x", "deadline_date": day(2)}, headers=P).status_code == 401
    assert create(headers=P).status_code == 401                                    # и сама практика выключена


# ══ создание: что пишется ════════════════════════════════════════════════════

def test_create_writes_exactly_what_was_asked_and_nothing_else():
    r = create({"title": "  Почистить колоду  ", "deadline_date": day(4), "category": "✨ Ритуал", "prio": "🔴"})
    assert r.status_code == 201, r.text
    [row] = rows()
    assert (row.title, row.category) == ("Почистить колоду", "✨ Ритуал")
    assert code_of(work_priority, row.priority_id) == "urgent" and code_of(work_status, row.status_id) == "open"
    assert row.deadline == aware(day(4), 0, 0)                                      # дата без времени: полночь локального дня (МСК)
    for col in ("client_id", "notes", "repeat_id", "day_of_week_id", "repeat_time", "reminder", "scheduled_at", "duration_min"):
        assert getattr(row, col) is None, col                                       # клиента, заметок, повтора, напоминания, слота нет
    assert row.user_id == MARK


def test_create_defaults_category_to_other_and_prio_to_important():
    r = create()
    assert r.status_code == 201
    [row] = rows()
    assert row.category == "🗂️ Прочее" and code_of(work_priority, row.priority_id) == "important"
    assert r.json()["category"] == "🗂️ Прочее" and r.json()["prio"] == "🟡"


@pytest.mark.parametrize("prio,code", [("🔴", "urgent"), ("🟡", "important"), ("⚪", "later"), ("⚪️", "later")])
def test_create_priorities_from_the_fixed_set(prio, code):
    assert create({"prio": prio}).status_code == 201
    assert code_of(work_priority, rows()[0].priority_id) == code


@pytest.mark.parametrize("prio", ["🟢", "high", "", "Срочно", "🔴🟡", None])
def test_create_unknown_priority_is_422(prio):
    assert create({"prio": prio}).status_code == 422 and rows() == []


@pytest.mark.parametrize("cat", CATEGORIES)
def test_create_every_whitelisted_category_by_code_and_by_name(cat):
    bare = cat.split(" ", 1)[1]
    for sent in (cat, bare, bare.lower(), bare.upper()):
        r = create({"category": sent})
        assert r.status_code == 201, (sent, r.text)
        assert r.json()["category"] == cat
    assert {row.category for row in rows()} == {cat}


@pytest.mark.parametrize("cat", ["Практика", "Клиенты", "👥 Клиенты", "Расклад для клиента", "Работа", "", " ", "Расходник", "x" * 50])
def test_create_unknown_category_is_422(cat):
    r = create({"category": cat})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_category" and rows() == []


@pytest.mark.parametrize("title,ok", [("а", True), ("я" * 120, True), ("я" * 121, False), ("", False), ("   ", False),
                                      ("две\nстроки", False), ("таб\tтут", False), ("<b>жирный</b>", False), ("a > b", False),
                                      ("нулевой\x00символ", False), ("разрыв строки", False), ("Ритуал на ясность & покой", True)])
def test_create_title_rules(title, ok):
    assert (create({"title": title}).status_code == 201) is ok


@pytest.mark.parametrize("extra", [{"client_id": "1"}, {"client_id": None}, {"client": "Анна"}, {"client_name": "Анна"}, {"notes": "заметка"},
                                   {"note": "заметка"}, {"repeat": "Ежедневно"}, {"reminder": "2030-01-01T10:00"},
                                   {"reminder_date": day(2)}, {"status": "done"}, {"user_id": "x"}, {"id": 1}, {"duration_min": 90},
                                   {"scheduled_at": "2030-01-01T10:00"}, {"for_sitter": True}, {"type": "клиентская"}, {"session_id": 1},
                                   {"ritual_id": 1}, {"shared": True}, {"date": day(2)}, {"x": 1}])
def test_create_rejects_any_field_beyond_the_five_allowed(extra):
    assert create(extra).status_code == 422 and rows() == []


def _upper_edge() -> int:
    today = local_now().date()
    return (rules.add_years(today, 2) - today).days                       # 730 или 731 дней: зависит от високосного года


@pytest.mark.parametrize("offset,ok", [(-1, True), (-2, False), (0, True), (365, True), ("edge", True), ("edge+1", False), (2000, False)])
def test_create_deadline_window_yesterday_to_two_years(offset, ok):
    offset = {"edge": _upper_edge(), "edge+1": _upper_edge() + 1}.get(offset, offset)
    assert (create({"deadline_date": day(offset)}).status_code == 201) is ok


@pytest.mark.parametrize("bad", ["", "2030-13-01", "2030-02-30", "30.01.2030", "2030-1-1", "завтра", "2030-01-01T10:00", None])
def test_create_bad_date_formats_are_422(bad):
    assert create({"deadline_date": bad}).status_code == 422


@pytest.mark.parametrize("bad", ["", "24:00", "9:00", "10:60", "10:5", "10-00", "утром", "10:00:00", "00:00"])
def test_create_bad_time_is_422_and_midnight_is_refused_because_it_means_no_time(bad):
    r = create({"deadline_time": bad})
    assert r.status_code == 422 and rows() == []


def test_create_deadline_time_boundaries():
    assert create({"deadline_time": "00:01"}).status_code == 201
    assert create({"deadline_time": "23:59"}).status_code == 201
    assert [r.deadline for r in rows()] == [aware(day(3), 0, 1), aware(day(3), 23, 59)]


def test_create_limit_20_per_day_and_refused_requests_do_not_count(monkeypatch):
    t = {"now": 0.0}
    monkeypatch.setattr(route, "_create_limiter", rules.DailyLimiter(20, clock=lambda: t["now"]))
    for i in range(5):
        assert create({"title": "", "deadline_date": day(1)}).status_code == 422             # отказы лимит не едят
    assert [create({"title": f"t{i}"}).status_code for i in range(20)] == [201] * 20
    r = create({"title": "лишняя"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit" and int(r.headers["retry-after"]) >= 1
    assert len(rows()) == 20
    t["now"] += 86401
    assert create({"title": "после суток"}).status_code == 201


def test_a_failed_insert_gives_the_limit_slot_back(monkeypatch):
    monkeypatch.setattr(route, "_create_limiter", rules.DailyLimiter(2))

    async def boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(route._works_repo, "create_personal", boom)
    c = api(raise_server_exceptions=False)
    assert c.post("/api/mcp/practice", json={"title": "x", "deadline_date": day(2)}, headers=P).status_code == 500
    assert route._create_limiter.used() == 0


def test_a_practice_nobody_owns_is_not_created(monkeypatch):
    async def none(tg_id):
        return ""
    monkeypatch.setattr(route, "get_user_id", none)
    r = create()
    assert r.status_code == 409 and r.json()["detail"]["code"] == "owner_unknown"
    assert rows() == [] and rows("") == []


def test_the_repo_refuses_an_empty_owner():
    assert asyncio.run(PgWorksRepo().create_personal("x", "Важно", None, "🗂️ Прочее", "", 3)) is None


# ══ без клиента: по конструкции ══════════════════════════════════════════════

def test_create_personal_has_no_way_to_name_a_client_a_note_or_a_repeat():
    params = set(inspect.signature(PgWorksRepo.create_personal).parameters)
    assert params == {"self", "title", "priority", "deadline", "category", "user_id", "tz_offset"}
    src = inspect.getsource(PgWorksRepo._create_personal_sync)
    tree = ast.parse(textwrap.dedent(src))
    keys = {k.value for n in ast.walk(tree) if isinstance(n, ast.Dict) for k in n.keys if isinstance(k, ast.Constant)}
    assert keys == {"title", "deadline", "category", "priority_id", "status_id", "user_id"}     # весь набор колонок INSERT, кроме слота


def test_the_route_never_passes_a_client_a_note_or_a_repeat_anywhere():
    tree = ast.parse(inspect.getsource(route))
    kwargs = {k.arg for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords}
    assert not kwargs & {"client_id", "notes", "repeat", "repeat_time", "day_of_week", "reminder"}, kwargs
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"create", "set_props", "set_deadline", "set_status", "mark_done", "schedule_reminder", "schedule_deadline_check",
                        "notify_user", "arcana_reminder_flow", "_schedule"}, names


def test_the_body_model_has_exactly_five_fields_and_forbids_the_rest():
    assert set(route.PracticeBody.model_fields) == {"title", "deadline_date", "deadline_time", "category", "prio"}
    assert route.PracticeBody.model_config["extra"] == "forbid"


# ══ ответ: только эхо ════════════════════════════════════════════════════════

def test_create_answer_has_only_the_echo_and_the_conflicts_and_no_secrets():
    r = create({"title": "Расклад на неделю", "deadline_date": day(5), "deadline_time": "18:30", "category": "🃏 Расклад", "prio": "⚪"})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"id", "title", "deadline", "deadline_time", "category", "prio", "conflicts"}
    assert body == {"id": body["id"], "title": "Расклад на неделю", "deadline": day(5), "deadline_time": "18:30",
                    "category": "🃏 Расклад", "prio": "⚪", "conflicts": []}
    assert str(OWNER) not in r.text and MARK not in r.text and TOKEN_P not in r.text and TOKEN_W not in r.text


def test_validation_errors_do_not_echo_the_token_or_the_owner():
    r = create({"deadline_date": "bad"})
    assert r.status_code == 422 and TOKEN_P not in r.text and str(OWNER) not in r.text and MARK not in r.text


# ══ побочные эффекты: ничего, кроме записи и алерта ══════════════════════════

@pytest.mark.parametrize("body", [{}, {"deadline_time": "10:00"}, {"deadline_time": "10:00", "category": "✨ Ритуал"},
                                  {"deadline_time": "10:00", "category": "🃏 Расклад", "prio": "🔴"}])
def test_no_reminder_no_deadline_ping_no_chat_message(env, body):
    assert create(body).status_code == 201
    env.reminder.assert_not_called()
    env.deadline_ping.assert_not_called()
    env.notify_user.assert_not_called()
    assert env.alert.await_count >= 1 and all(c.args[1] == (config.log_thread_kaimcp or config.log_thread_nexus) for c in env.alert.await_args_list)


def test_alert_says_what_happened_and_holds_nothing_extra(env):
    create({"title": "Чистка & очищение", "deadline_date": day(3), "deadline_time": "09:15", "category": "📚 Обучение"})
    [call] = env.alert.await_args_list
    text = call.args[0]
    assert text == f"🤖 Claude создал практику: <b>Чистка &amp; очищение</b>, срок {day(3)} 09:15"
    assert TOKEN_P not in text and str(OWNER) not in text and MARK not in text


def test_alert_goes_to_the_kaimcp_topic_when_configured(monkeypatch, env):
    monkeypatch.setattr(config, "log_thread_kaimcp", "777")
    create()
    assert env.alert.await_args_list[0].args[1] == "777"


def test_alert_failure_does_not_break_the_request(monkeypatch, env):
    env.alert.side_effect = RuntimeError("telegram down")
    assert create().status_code == 201 and len(rows()) == 1


# ══ занятость Зари (пункт 2: проверено кодом, закреплено тестом) ═════════════

@pytest.mark.parametrize("cat", CATEGORIES)
def test_a_date_only_practice_changes_nothing_in_zarya_in_any_category(cat):
    before = zarya_state()
    r = create({"category": cat, "deadline_date": day(2)})                       # дата без времени
    assert r.status_code == 201, r.text
    [row] = rows()
    assert row.scheduled_at is None and row.duration_min is None                # слота нет: busy.py и agenda.py читают только scheduled_at
    assert zarya_state() == before                                               # busy_intervals (слоты, календарь, ICS) и day_agenda не изменились
    assert r.json()["conflicts"] == []


@pytest.mark.parametrize("cat", CATEGORIES)
def test_a_timed_practice_takes_a_slot_only_in_spread_and_ritual(cat):
    before = zarya_state()
    r = create({"category": cat, "deadline_date": day(2), "deadline_time": "19:00"})
    assert r.status_code == 201, r.text
    [row] = rows()
    after = zarya_state()
    if cat in SLOT_CATEGORIES:
        assert row.scheduled_at == row.deadline == aware(day(2), 19, 0) and row.duration_min == 60
        added = [b for b in after["busy"] if b not in before["busy"]]
        assert [(s, e, src, ad) for s, e, src, ad in added] == [
            (aware(day(2), 19, 0).astimezone(timezone.utc), aware(day(2), 20, 0).astimezone(timezone.utc), "work", False)]
        assert len(after["agenda"]) == len(before["agenda"]) + 1
    else:
        assert row.scheduled_at is None and row.duration_min is None
        assert after == before


def test_a_closed_practice_gives_its_slot_back():
    """Закрытая работа не держит слот (busy.py): выполненная практика не должна висеть в календаре Зари."""
    before = zarya_state()
    create({"category": "✨ Ритуал", "deadline_time": "19:00", "deadline_date": day(2)})
    assert zarya_state() != before
    with get_engine().begin() as c:
        done = c.execute(select(work_status.c.id).where(work_status.c.code == "done")).scalar()
        c.execute(works_table.update().where(works_table.c.user_id == MARK).values(status_id=done))
    assert zarya_state() == before


# ══ пересечение с бронями друзей (D2, только чтение) ═════════════════════════

def test_a_timed_ritual_that_overlaps_a_booking_warns_with_date_and_time_only(env):
    make_booking(aware(day(3), 10, 30), aware(day(3), 11, 30))
    before = _booking_table()
    r = create({"category": "✨ Ритуал", "deadline_date": day(3), "deadline_time": "10:00"})
    assert r.status_code == 201
    assert r.json()["conflicts"] == [{"date": day(3), "starts": "10:30", "ends": "11:30"}]
    assert "DruzhokUnique17" not in r.text and "contact-unique" not in r.text and "TopicUnique999" not in r.text
    texts = [c.args[0] for c in env.alert.await_args_list]
    assert any("DruzhokUnique17" in t and "Практика #" in t for t in texts)         # имя друга только в закрытом топике
    assert "contact-unique" not in " ".join(texts) and "TopicUnique999" not in " ".join(texts)
    assert _booking_table() == before                                               # брони не тронуты


def test_adjacent_booking_is_not_an_overlap_and_other_states_do_not_count():
    make_booking(aware(day(3), 9, 0), aware(day(3), 10, 0))
    make_booking(aware(day(3), 11, 0), aware(day(3), 12, 0))
    make_booking(aware(day(3), 10, 0), aware(day(3), 11, 0), status="cancelled_by_owner")
    r = create({"category": "🃏 Расклад", "deadline_date": day(3), "deadline_time": "10:00"})
    assert r.status_code == 201 and r.json()["conflicts"] == []


@pytest.mark.parametrize("body", [{"category": "✨ Ритуал"}, {"category": "📱 Соцсети", "deadline_time": "10:30"},
                                  {"category": "🗂️ Прочее", "deadline_time": "10:30"}])
def test_a_practice_without_a_slot_has_no_conflicts_and_never_reads_bookings(monkeypatch, body):
    make_booking(aware(day(3), 0, 0), aware(day(3), 23, 0))                        # бронь на весь день
    import core.booking.repo as bkrepo
    spy = AsyncMock(side_effect=AssertionError("bookings must not be read for a practice without a slot"))
    monkeypatch.setattr(bkrepo, "list_bookings", spy)
    r = create({"deadline_date": day(3), **body})
    assert r.status_code == 201 and r.json()["conflicts"] == []
    spy.assert_not_called()


def test_conflicts_is_null_when_bookings_cannot_be_read_and_the_practice_is_still_written(monkeypatch):
    import core.booking.repo as bkrepo
    monkeypatch.setattr(bkrepo, "list_bookings", AsyncMock(side_effect=RuntimeError("db")))
    r = create({"category": "✨ Ритуал", "deadline_date": day(3), "deadline_time": "10:00"})
    assert r.status_code == 201 and r.json()["conflicts"] is None and len(rows()) == 1


def _booking_table() -> list:
    with get_engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(select(booking).order_by(booking.c.id))]


# ══ имя клиента в названии ═══════════════════════════════════════════════════

@pytest.mark.parametrize("title", [
    "Расклад для Марии", "расклад МАРИИ", "РАСКЛАД ДЛЯ МАРИЯ", "Петровой отправить", "ритуал Петрова", "для Олега",
    "Олегов ритуал", "олег", "Ритуал для Игоря", "Игорю расклад", "Верой займусь", "для веры", "Игорь, Мария и Олег",
])
def test_a_title_with_a_client_name_is_422_without_saying_which_name(env, title):
    r = create({"title": title})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "client_name_in_title"
    assert rows() == [] and route._create_limiter.used() == 0                      # не записано, лимит не потрачен
    low = r.text.lower()
    assert not any(n in low for n in ("мари", "петров", "олег", "игор", "вер"))      # ответ не подтверждает и не называет имя
    env.alert.assert_not_called()


@pytest.mark.parametrize("title", [
    "верно ли это", "вернуть свечи", "Мариновать травы", "Яндекс календарь", "Ананасы", "Оливковый чай",
    "Расклад для себя", "Чистка колоды", "Ритуал на ясность", "Игра в карты", "Игровая карта", "Петровка", "Марс",
    "Ян", "Тестовая Кай",                                                           # короткое имя (<3 букв) не ищем; карточка «Я» разрешена
])
def test_ordinary_words_and_own_card_are_not_blocked(title):
    assert create({"title": title}).status_code == 201


def test_matching_is_whole_word_case_insensitive_and_yo_equals_ye():
    names = ["Алёна Смирнова", "Anna"]
    assert rules.title_has_client_name("для Алены", names) and rules.title_has_client_name("АЛЁНЫ карта", names)
    assert rules.title_has_client_name("смирнова", names) and rules.title_has_client_name("Anna", names) and rules.title_has_client_name("anna!", names)
    assert not rules.title_has_client_name("аленький цветочек", names)              # часть слова не считается
    assert not rules.title_has_client_name("annabel", names)
    assert not rules.title_has_client_name("", names) and not rules.title_has_client_name("для Алены", [])


def test_a_failure_reading_clients_refuses_the_request_and_writes_nothing(env):
    async def boom(user_id=""):
        raise RuntimeError("db")
    env.clients.list_all = boom
    r = create()
    assert r.status_code == 503 and r.json()["detail"]["code"] == "client_check_failed"
    assert rows() == [] and route._create_limiter.used() == 0


def test_the_rejection_log_has_neither_the_title_nor_the_name(caplog):
    with caplog.at_level("DEBUG"):
        create({"title": "Расклад для Марии"})
    assert "Мари" not in caplog.text and "Расклад" not in caplog.text


def test_the_own_client_card_is_not_in_the_name_list(env):
    assert create({"title": "Расклад для Тестовой Кай"}).status_code == 201
    assert env.clients.calls == 1


# ══ главный тест: обход ВСЕХ маршрутов ═══════════════════════════════════════

PRACTICE_ROUTE = ("POST", "/api/mcp/practice")
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _gated_routes() -> list:
    def has_gate(dependant) -> bool:
        return any(d.call is gate.mcp_practice_gate or has_gate(d) for d in dependant.dependencies)
    return sorted((m, r.path) for r in app.routes if isinstance(r, APIRoute) and has_gate(r.dependant) for m in sorted(r.methods))


def test_the_practice_gate_is_attached_to_exactly_one_route():
    assert _gated_routes() == [PRACTICE_ROUTE]


def test_there_is_no_way_to_move_edit_or_delete_through_the_practice_route():
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert api().request(method, "/api/mcp/practice", headers=P).status_code in (404, 405)
    paths = {r.path for r in app.routes if isinstance(r, APIRoute) and "/mcp/practice" in r.path}
    assert paths == {"/api/mcp/practice"}


def _walk(headers) -> dict:
    import re
    paths: dict = {}
    for r in app.routes:
        p, ms = getattr(r, "path", None), getattr(r, "methods", None)
        if p:
            paths.setdefault(p, set()).update(ms or [])
    c = api(raise_server_exceptions=False)
    norm = lambda code: 401 if code == 429 else code                           # noqa: E731
    out = {}
    for path, declared in sorted(paths.items()):
        url = re.sub(r"\{[^}]*\}", "1", path)
        for method in sorted((set(declared) | set(ALL_METHODS)) - {"HEAD", "OPTIONS"}):
            kw = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            with_t = c.request(method, url, headers=headers, **kw).status_code
            without = c.request(method, url, **kw).status_code
            out[(method, path)] = (norm(with_t), norm(without), with_t)
    return out


def test_practice_token_opens_exactly_the_one_new_post_across_all_routes():
    """ГЛАВНЫЙ тест: все маршруты приложения (app.routes) всеми методами с токеном практики."""
    walk = _walk(P)
    assert len(walk) > 500
    for prefix in ("/api/finance", "/api/arcana", "/api/cats", "/api/cat-health", "/api/care", "/api/memory", "/api/people",
                   "/api/booking", "/api/lists", "/api/tasks", "/api/mcp"):
        assert any(p.startswith(prefix) for _, p in walk), prefix
    opened = {k for k, (w, wo, _) in walk.items() if w != wo}                  # токен изменил исход запроса
    assert opened == {PRACTICE_ROUTE}, sorted(opened ^ {PRACTICE_ROUTE})
    bad = [f"{m} {p} -> {raw}" for (m, p), (w, wo, raw) in walk.items()
           if (m, p) != PRACTICE_ROUTE and w not in (401, 403, 404, 405, 422) and w != wo]
    assert not bad, "\n".join(bad)
    assert all(raw < 500 for _, _, raw in walk.values())
    # существующее создание работы из Mini App по токену практики закрыто
    assert walk[("POST", "/api/arcana/works")][0] == 401
    for key in (("GET", "/api/tasks"), ("GET", "/api/arcana/reminders"), ("POST", "/api/mcp/tasks")):
        assert walk[key][0] == 401, key


def test_read_and_write_tokens_open_nothing_new_on_the_practice_route():
    for token in (TOKEN_R, TOKEN_W):
        walk = _walk({"Authorization": f"Bearer {token}"})
        assert walk[PRACTICE_ROUTE][0] == 401
        opened = {k for k, (w, wo, _) in walk.items() if w != wo}
        assert PRACTICE_ROUTE not in opened


def test_mini_app_works_route_still_needs_a_normal_login(monkeypatch):
    assert api().post("/api/arcana/works", json={"title": "x"}, headers=P).status_code == 401
