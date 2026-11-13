"""tests/test_mcp_read_token.py — служебное чтение GET /api/tasks и GET /api/arcana/reminders по Bearer-токену для kai-mcp
(по образцу ADR-0033, ADR-0038).

Путь авторизации настоящий (current_user_id не подменяется), initData и cookie подписываются здесь же. Токен и tg_id
владельца вымышленные, подставляются в config только на время теста. Репозиторий задач и часовой пояс подменены,
чтобы тест не зависел от данных в базе; «главный» тест обходит ВСЕ маршруты приложения.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select

from core.config import config
from core.db import get_engine
from miniapp.backend import (care_period_auth, care_write_auth, cats_read_auth, cats_write_auth, mcp_read_auth as gate,
                             mcp_write_auth, sitter_lists_auth)
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes import arcana_today as arcana_route
from miniapp.backend.routes import tasks as tasks_route
from miniapp.backend.tg_auth import issue_session
from nexus.repos import pg_tasks_repo
from nexus.repos.pg_tasks_repo import PgTasksRepo, Task
from arcana.repos.clients_tables import clients as clients_table
from arcana.repos.works_repo import Work
from arcana.repos.works_tables import work_priority, work_status, works as works_table
from nexus.repos.tasks_tables import task_category, task_status, tasks as tasks_table

TOKEN = "test-only-mcp-token-" + "b2" * 10                      # вымышленный, 40 символов
OWNER = 700000123                                               # вымышленный tg_id владельца токена
OTHER_OWNER = 700000001                                         # другой разрешённый пользователь (обычный вход)
PUBLIC_PEER = ("8.8.8.8", 5000)                                 # настоящий публичный адрес
OK = {"Authorization": f"Bearer {TOKEN}"}
SECRET = "test-session-secret-for-mcp-tests"


class FakeTasksRepo:
    """Задачи двух пользователей: по Bearer читаются только задачи MCP-владельца."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def _rows(self, user_id: str):
        self.asked.append(user_id)
        who = {f"user-{OWNER}": "владельца токена", f"user-{OTHER_OWNER}": "обычного входа"}.get(user_id, "чужого")
        return [Task(id="t1", title=f"Задача {who}", status="Not started", priority="🔴 Срочно", category="💻 Подписки",
                     deadline="2026-11-12T09:00:00+03:00", note="заметка", user_id=user_id)]

    async def active(self, user_id: str):
        return self._rows(user_id)

    async def list_all(self, user_id: str):
        return self._rows(user_id)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(config, "mcp_read_token", TOKEN)
    monkeypatch.setattr(config, "mcp_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER, OTHER_OWNER])
    monkeypatch.setattr(config, "session_secret", SECRET)
    for mod in (gate, mcp_write_auth, cats_read_auth, sitter_lists_auth, care_write_auth, care_period_auth, cats_write_auth):
        monkeypatch.setattr(mod, "_limiter", cats_read_auth.FailureLimiter())   # состояние лимитов не течёт между тестами
    repo = FakeTasksRepo()
    seen: dict = {"tg_ids": []}

    async def tz(tg_id):
        seen["tg_ids"].append(tg_id)
        return date(2026, 11, 12), 3

    monkeypatch.setattr(tasks_route, "_tasks_repo", repo)
    monkeypatch.setattr(tasks_route, "today_user_tz", tz)
    monkeypatch.setattr(tasks_route, "get_user_id", AsyncMock(side_effect=lambda tg_id: f"user-{tg_id}"))
    app.dependency_overrides.pop(current_user_id, None)
    yield seen
    app.dependency_overrides.pop(current_user_id, None)


def api(peer=PUBLIC_PEER, **kw):
    """TestClient, у которого сервер видит peer-адрес."""
    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": peer}
        await app(scope, receive, send)
    return TestClient(with_peer, **kw)


def init_data(tg_id: int) -> str:
    user = json.dumps({"id": tg_id, "first_name": "Test"}, separators=(",", ":"))
    pairs = sorted([("auth_date", str(int(time.time()))), ("user", user)])
    check = "\n".join(f"{k}={v}" for k, v in pairs)
    secret = hmac.new(b"WebAppData", config.nexus.tg_token.encode(), hashlib.sha256).digest()
    return urlencode(pairs + [("hash", hmac.new(secret, check.encode(), hashlib.sha256).hexdigest())])


def titles(resp) -> list[str]:
    return [t["title"] for t in resp.json()["tasks"]]


# ── верный токен ─────────────────────────────────────────────────────────────

def test_valid_bearer_reads_tasks_as_the_configured_owner(env):
    r = api().get("/api/tasks", headers=OK)
    assert r.status_code == 200
    assert titles(r) == ["Задача владельца токена"]                      # задачи именно MCP_READ_OWNER_TG_ID
    assert env["tg_ids"] == [OWNER]
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    body = r.json()
    assert set(body) == {"filter", "total", "tasks"} and body["total"] == 1


def test_scheme_is_case_insensitive():
    assert api().get("/api/tasks", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200
    assert api().get("/api/tasks", headers={"Authorization": f"BEARER {TOKEN}"}).status_code == 200


@pytest.mark.parametrize("flt", ["active", "overdue", "today", "done", "all"])
def test_every_filter_and_limit_work_with_the_token(flt):
    r = api().get("/api/tasks", params={"filter": flt, "limit": 5}, headers=OK)
    assert r.status_code == 200 and r.json()["filter"] == flt


# ── неверный Bearer ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("header", [
    "Bearer wrong-token", "Bearer", "Bearer   ", "bearer", "Bearer " + TOKEN[:-1], "Bearer " + TOKEN + "x",
    "Bearer " + TOKEN.upper(),
])
def test_bad_bearer_is_401(header):
    r = api().get("/api/tasks", headers={"Authorization": header})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_bad_bearer_is_rejected_even_with_valid_normal_auth():
    r = api().get("/api/tasks", headers={"Authorization": "Bearer wrong", "X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert r.status_code == 401                                          # неверный токен не «спасается» валидным initData


# ── без заголовка и с другой схемой: обычный вход, как раньше ────────────────

def test_without_authorization_header_normal_auth_applies(env):
    c = api()
    assert c.get("/api/tasks").status_code == 401                        # нет initData и cookie
    r = c.get("/api/tasks", headers={"X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert r.status_code == 200 and titles(r) == ["Задача обычного входа"]      # initData: свой пользователь, не MCP-владелец
    assert env["tg_ids"] == [OTHER_OWNER]
    cookie, _ = issue_session(OTHER_OWNER, secret=SECRET, remember=False)
    r = api().get("/api/tasks", cookies={"hl_session": cookie})
    assert r.status_code == 200 and titles(r) == ["Задача обычного входа"]      # cookie hl_session не сломан


@pytest.mark.parametrize("header", [f"tma {'x' * 30}", "tma", "Basic dXNlcjpwYXNz", "Digest username=x", "Negotiate abc",
                                    "", "   ", f"Token {TOKEN}", f"Basic {TOKEN}", TOKEN, "Bearerx abc"])
def test_other_authorization_scheme_is_normal_login_not_401_by_the_gate(header, env):
    """Заголовок Authorization с другой схемой (например `tma <initData>`) идёт обычным входом."""
    c = api()
    r = c.get("/api/tasks", headers={"Authorization": header, "X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert r.status_code == 200 and titles(r) == ["Задача обычного входа"]
    assert env["tg_ids"][-1] == OTHER_OWNER                              # личность из initData, не из токена
    cookie, _ = issue_session(OTHER_OWNER, secret=SECRET, remember=False)
    assert c.get("/api/tasks", headers={"Authorization": header}, cookies={"hl_session": cookie}).status_code == 200
    # без обычного входа такой заголовок ничего не открывает (в том числе верный токен под чужой схемой)
    r = api().get("/api/tasks", headers={"Authorization": header})
    assert r.status_code == 401 and "mcp" not in r.text.lower()


def test_other_scheme_is_not_counted_as_a_failed_bearer_attempt():
    c = api()
    for _ in range(30):                                                  # много «чужих» схем подряд
        c.get("/api/tasks", headers={"Authorization": "tma something", "X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert c.get("/api/tasks", headers=OK).status_code == 200            # лимит не тронут: верный токен проходит


# ── приватность: задачи практики не уходят в MCP (ADR-0038) ──────────────────

HIDDEN_CATEGORIES = ["🔮 Практика", "🕯️ Расходники", "🕯 Расходники", "🔮 практика", "Расходники"]


class MixedRepo:
    """Видимые и скрытые задачи; скрытые стоят раньше по сроку, чтобы limit без фильтра их бы зацепил."""

    def __init__(self) -> None:
        self.rows = [Task(id=f"h{i}", title=f"СКРЫТО-{i}", status="Not started" if i % 2 else "Done", category=cat,
                          deadline="2026-11-10T09:00:00+03:00", completed_at="2026-11-10T10:00:00+03:00" if i % 2 == 0 else "")
                     for i, cat in enumerate(HIDDEN_CATEGORIES)]
        self.rows += [Task(id="v1", title="Купить корм", status="Not started", category="🐾 Коты",
                           deadline="2026-11-14T09:00:00+03:00"),
                      Task(id="v2", title="Оплатить подписку", status="Not started", category="💻 Подписки",
                           deadline="2026-11-15T09:00:00+03:00"),
                      Task(id="v3", title="Сделано давно", status="Done", category="💳 Прочее",
                           completed_at="2026-11-01T10:00:00+03:00")]

    async def active(self, user_id: str):
        return [t for t in self.rows if t.status != "Done"]

    async def list_all(self, user_id: str):
        return list(self.rows)


@pytest.mark.parametrize("flt", ["active", "overdue", "today", "done", "all"])
def test_practice_tasks_are_not_in_the_mcp_answer_nor_in_total(monkeypatch, flt):
    monkeypatch.setattr(tasks_route, "_tasks_repo", MixedRepo())
    r = api().get("/api/tasks", params={"filter": flt, "limit": 100}, headers=OK)
    assert r.status_code == 200
    body = r.json()
    assert not [t for t in titles(r) if t.startswith("СКРЫТО")]
    assert body["total"] == len(body["tasks"])                          # скрытые не входят и в total
    assert all(t["cat"]["name"] not in ("Практика", "Расходники", "практика") for t in body["tasks"])


def test_practice_filter_applies_before_limit_and_total(monkeypatch):
    monkeypatch.setattr(tasks_route, "_tasks_repo", MixedRepo())
    r = api().get("/api/tasks", params={"filter": "all", "limit": 1}, headers=OK)
    assert titles(r) == ["Купить корм"]                                 # без фильтра первой была бы скрытая (срок раньше)
    assert r.json()["total"] == 3                                       # Купить корм, Оплатить подписку, Сделано давно


def test_without_bearer_normal_login_still_sees_everything(monkeypatch):
    monkeypatch.setattr(tasks_route, "_tasks_repo", MixedRepo())
    c = api()
    r = c.get("/api/tasks", params={"filter": "all", "limit": 100}, headers={"X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert r.status_code == 200
    assert len(titles(r)) == len(HIDDEN_CATEGORIES) + 3 and r.json()["total"] == len(HIDDEN_CATEGORIES) + 3
    cookie, _ = issue_session(OTHER_OWNER, secret=SECRET, remember=False)
    r = api().get("/api/tasks", params={"filter": "all", "limit": 100}, cookies={"hl_session": cookie})
    assert len([t for t in titles(r) if t.startswith("СКРЫТО")]) == len(HIDDEN_CATEGORIES)
    # другая схема Authorization с обычным входом тоже без фильтра
    r = api().get("/api/tasks", params={"filter": "all"}, headers={"Authorization": "tma x",
                                                                    "X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert len([t for t in titles(r) if t.startswith("СКРЫТО")]) == len(HIDDEN_CATEGORIES)


@pytest.fixture
def db_tasks(monkeypatch):
    """Настоящие строки в базе: категории практики добавлены в справочник, задачи лежат в таблице tasks."""
    mark = "test-mcp-read-token"
    added = []
    with get_engine().begin() as c:
        have = {r[0] for r in c.execute(select(task_category.c.code))}
        for code in ("🔮 Практика", "🕯️ Расходники"):
            if code not in have:
                c.execute(insert(task_category).values(code=code))
                added.append(code)
    for d in (pg_tasks_repo._status_id, pg_tasks_repo._status_code, pg_tasks_repo._category_id, pg_tasks_repo._category_code,
              pg_tasks_repo._priority_id, pg_tasks_repo._priority_code, pg_tasks_repo._repeat_id, pg_tasks_repo._repeat_code,
              pg_tasks_repo._dow_id, pg_tasks_repo._dow_code):
        d.clear()                                                       # справочники перечитаются вместе с новыми строками
    with get_engine().begin() as c:
        sid = c.execute(select(task_status.c.id).where(task_status.c.code == "Not started")).scalar()
        cats = {code: cid for cid, code in c.execute(select(task_category.c.id, task_category.c.code))}
        for title, cat in (("Закупка свечей СКРЫТО", "🕯️ Расходники"), ("Подготовить ритуал СКРЫТО", "🔮 Практика"),
                           ("Запись к врачу", "🏥 Здоровье"), ("Прочее дело", "💳 Прочее")):
            c.execute(insert(tasks_table).values(title=title, status_id=sid, category_id=cats[cat], user_id=mark))
    monkeypatch.setattr(tasks_route, "_tasks_repo", PgTasksRepo())
    monkeypatch.setattr(tasks_route, "get_user_id", AsyncMock(return_value=mark))
    yield mark
    with get_engine().begin() as c:
        c.execute(delete(tasks_table).where(tasks_table.c.user_id == mark))
        if added:
            c.execute(delete(task_category).where(task_category.c.code.in_(added)))
    for d in (pg_tasks_repo._status_id, pg_tasks_repo._status_code, pg_tasks_repo._category_id, pg_tasks_repo._category_code):
        d.clear()


def test_practice_tasks_in_the_database_are_hidden_from_mcp_only(db_tasks):
    r = api().get("/api/tasks", params={"filter": "all", "limit": 100}, headers=OK)
    assert r.status_code == 200 and sorted(titles(r)) == ["Запись к врачу", "Прочее дело"] and r.json()["total"] == 2
    r = api().get("/api/tasks", params={"filter": "active", "limit": 1}, headers=OK)
    assert len(titles(r)) == 1 and "СКРЫТО" not in titles(r)[0] and r.json()["total"] == 2
    r = api().get("/api/tasks", params={"filter": "all", "limit": 100}, headers={"X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert len(titles(r)) == 4 and r.json()["total"] == 4              # обычный вход видит всё, как раньше


# ── выключено / неверно настроено ────────────────────────────────────────────

@pytest.mark.parametrize("token", ["", "short-token", "x" * 31])
def test_disabled_when_token_missing_or_too_short(monkeypatch, token):
    monkeypatch.setattr(config, "mcp_read_token", token)
    for sent in (token, "", "wrong"):                                    # пустой токен не должен «совпасть» с пустым
        assert api().get("/api/tasks", headers={"Authorization": f"Bearer {sent}"}).status_code == 401


def test_disabled_when_owner_not_configured_or_not_allowed(monkeypatch):
    monkeypatch.setattr(config, "mcp_read_owner_tg_id", 0)
    assert api().get("/api/tasks", headers=OK).status_code == 401
    monkeypatch.setattr(config, "mcp_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OTHER_OWNER])            # владелец токена не в списке разрешённых
    assert api().get("/api/tasks", headers=OK).status_code == 401


@pytest.mark.parametrize("other", ["cats_read_token", "sitter_lists_token", "care_write_token", "care_period_token",
                                   "cats_write_token", "booking_service_token"])
def test_disabled_when_token_equals_another_service_token(monkeypatch, other):
    monkeypatch.setattr(config, other, TOKEN)
    assert api().get("/api/tasks", headers=OK).status_code == 401


def test_tokens_are_not_interchangeable_with_the_cats_gate(monkeypatch):
    cats_tok = "test-only-cats-token-" + "c3" * 10
    monkeypatch.setattr(config, "cats_read_token", cats_tok)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    assert api().get("/api/tasks", headers={"Authorization": f"Bearer {cats_tok}"}).status_code == 401
    assert api().get("/api/cats", headers=OK).status_code == 401


# ── hmac.compare_digest и логи ───────────────────────────────────────────────

def test_token_is_compared_with_hmac_compare_digest():
    with patch.object(gate.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        assert api().get("/api/tasks", headers=OK).status_code == 200
        assert api().get("/api/tasks", headers={"Authorization": "Bearer wrong-token-value"}).status_code == 401
    assert spy.call_count == 2
    assert all(isinstance(a, bytes) for call in spy.call_args_list for a in call.args)


def test_token_value_never_reaches_logs(caplog):
    caplog.set_level(logging.DEBUG)
    bad = "Bearer " + "q" * 40
    api().get("/api/tasks", headers=OK)
    api().get("/api/tasks", headers={"Authorization": bad})
    assert TOKEN not in caplog.text and ("q" * 40) not in caplog.text
    assert "bad bearer" in caplog.text                                   # сама попытка залогирована, без значения


# ── лимит неверных попыток ───────────────────────────────────────────────────

def _fake_clock():
    t = {"now": 1000.0}
    return t, (lambda: t["now"])


def test_rate_limit_blocks_after_ten_bad_attempts_even_for_valid_token(monkeypatch):
    t, clock = _fake_clock()
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=clock))
    bad = {"Authorization": "Bearer wrong"}
    c = api()
    assert [c.get("/api/tasks", headers=bad).status_code for _ in range(10)] == [401] * 10
    r = c.get("/api/tasks", headers=bad)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60
    assert c.get("/api/tasks", headers=OK).status_code == 429            # пока окно не освободилось, и верному тоже
    t["now"] += 61
    assert c.get("/api/tasks", headers=OK).status_code == 200


def test_valid_requests_are_never_counted(monkeypatch):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    c = api()
    assert all(c.get("/api/tasks", headers=OK).status_code == 200 for _ in range(40))


def test_limit_is_per_ip(monkeypatch):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    attacker, friend = api(("8.8.8.8", 1)), api(("8.8.4.4", 1))
    for _ in range(11):
        attacker.get("/api/tasks", headers={"Authorization": "Bearer w"})
    assert attacker.get("/api/tasks", headers=OK).status_code == 429
    assert friend.get("/api/tasks", headers=OK).status_code == 200


def test_the_limit_is_its_own_window_not_shared_with_other_gates(monkeypatch):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    monkeypatch.setattr(cats_read_auth, "_limiter", cats_read_auth.FailureLimiter(clock=_fake_clock()[1]))
    c = api()
    for _ in range(11):
        c.get("/api/cats", headers={"Authorization": "Bearer w"})        # исчерпали окно cats
    assert c.get("/api/cats", headers={"Authorization": "Bearer w"}).status_code == 429
    assert c.get("/api/tasks", headers=OK).status_code == 200            # окно MCP-шлюза отдельное


# ── токен читает ТОЛЬКО GET /api/tasks ───────────────────────────────────────

def _gated_routes() -> list[tuple[str, str]]:
    def has_gate(dependant) -> bool:
        return any(d.call is gate.mcp_read_gate or has_gate(d) for d in dependant.dependencies)
    return sorted((m, r.path) for r in app.routes if isinstance(r, APIRoute) and has_gate(r.dependant)
                  for m in sorted(r.methods))


def test_the_gate_is_attached_to_exactly_two_routes():
    assert _gated_routes() == [("GET", "/api/arcana/reminders"), ("GET", "/api/tasks")]


def test_writes_on_tasks_and_neighbours_stay_closed_with_the_token():
    c = api()
    for method, path, kw in (("POST", "/api/tasks", {"json": {"title": "x"}}), ("PATCH", "/api/tasks/1", {"json": {}}),
                             ("DELETE", "/api/tasks/1", {}), ("POST", "/api/tasks/1/done", {}),
                             ("POST", "/api/memory", {"json": {}}), ("GET", "/api/finance/month", {}),
                             ("GET", "/api/memory", {}), ("GET", "/api/people", {})):
        assert c.request(method, path, headers=OK, **kw).status_code in (401, 403, 404, 405, 422), (method, path)


# ── GET /api/arcana/reminders: только время и тип, без данных клиентов (ADR-0038) ──────────────

TODAY = date(2026, 11, 12)                                              # «сегодня» владельца, пояс +3
TZ = timezone(timedelta(hours=3))
FIVE_KEYS = {"at", "kind", "category", "priority", "overdue"}
SECRETS = ["ClientUnique123", "TitleUnique456", "NoteUnique789", "PriceUnique98765", "ClientContact555",
           "ClientRequest666", "ClientNotes777", "SecretCategoryXYZ"]


def at(day_offset: int, hh: int = 10, mm: int = 0) -> datetime:
    return datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm, tzinfo=TZ) + timedelta(days=day_offset)


@pytest.fixture
def arcana_db(monkeypatch):
    """Настоящие строки в базе: клиент с приметными строками и работы разных статусов, дат и категорий."""
    mark, other = "test-mcp-arcana-reminders", "test-mcp-arcana-other-owner"
    monkeypatch.setattr(arcana_route, "get_user_id", AsyncMock(side_effect=lambda tg_id: mark))
    async def tz(tg_id):
        return TODAY, 3
    monkeypatch.setattr(arcana_route, "today_user_tz", tz)
    ids: dict[str, int] = {}
    with get_engine().begin() as c:
        st = {r[1]: r[0] for r in c.execute(select(work_status.c.id, work_status.c.code))}
        pr = {r[1]: r[0] for r in c.execute(select(work_priority.c.id, work_priority.c.code))}
        cid = c.execute(insert(clients_table).values(
            name="ClientUnique123", contact="ClientContact555", request="ClientRequest666", notes="ClientNotes777",
            user_id=mark).returning(clients_table.c.id)).scalar()
        other_cid = c.execute(insert(clients_table).values(name="OtherClient", user_id=other)
                              .returning(clients_table.c.id)).scalar()

        def work(key, *, status="open", prio="urgent", cat="🃏 Расклад", reminder=None, deadline=None, owner=mark,
                 title="TitleUnique456", notes="NoteUnique789; PriceUnique98765 руб.", client=cid):
            ids[key] = c.execute(insert(works_table).values(
                title=title, notes=notes, category=cat, status_id=st[status], priority_id=pr[prio], client_id=client,
                reminder=reminder, deadline=deadline, user_id=owner).returning(works_table.c.id)).scalar()

        work("today", reminder=at(0, 9))                                          # сегодня, напоминание
        work("today_deadline_only", cat="✨ Ритуал", prio="important", deadline=at(0, 18))
        work("today_past_hour", deadline=at(0, 7), cat="📱 Соцсети", prio="later")      # срок сегодня утром: ещё не просрочено
        work("in3", cat="📚 Обучение", reminder=at(3))
        work("in7", cat="🛒 Расходники", reminder=at(7, 23, 30))                       # последний день окна days=7
        work("in8", reminder=at(8))
        work("in31", reminder=at(31))
        work("in32", reminder=at(32))
        work("overdue_yesterday", deadline=at(-1, 23, 59), cat="✨ Ритуал")
        work("overdue_old_with_reminder", deadline=at(-20), reminder=at(2), cat="🗂️ Прочее")
        work("weird_category", cat="SecretCategoryXYZ", reminder=at(1))
        work("done", status="done", reminder=at(1))
        work("archived", status="archived", reminder=at(1))
        work("scheduled", status="scheduled", reminder=at(4), cat="🃏 Расклад")        # запись из букинга: тоже открытая
        work("no_dates", reminder=None, deadline=None)
        work("other_owner", owner=other, reminder=at(1), client=other_cid, title="OtherTitle", notes="OtherNote")
    yield ids
    with get_engine().begin() as c:
        c.execute(delete(works_table).where(works_table.c.user_id.in_([mark, other])))
        c.execute(delete(clients_table).where(clients_table.c.user_id.in_([mark, other])))


def reminders(days=None, headers=OK, **kw):
    params = {} if days is None else {"days": days}
    return api().get("/api/arcana/reminders", params=params, headers=headers, **kw)


def test_reminders_privacy_no_client_title_note_price_or_extra_keys(arcana_db):
    r = reminders(31)
    assert r.status_code == 200
    raw = r.text                                                          # сырое тело, а не разобранный JSON
    for secret in SECRETS:
        assert secret not in raw, f"в ответе нашлось {secret}"
    for other in ("OtherClient", "OtherTitle", "OtherNote"):
        assert other not in raw                                           # чужой владелец не попадает
    body = r.json()
    assert set(body) == {"days", "items"} and body["days"] == 31 and body["items"]
    assert all(set(item) == FIVE_KEYS for item in body["items"])
    assert all(item["kind"] == "work" and isinstance(item["overdue"], bool) for item in body["items"])
    assert not any(str(i) in raw for i in arcana_db.values() if len(str(i)) > 3)        # id работ не отдаются
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"


def test_reminders_categories_and_priorities_come_from_white_lists(arcana_db):
    items = reminders(31).json()["items"]
    cats = {i["category"] for i in items}
    assert cats <= {"🃏 Расклад", "✨ Ритуал", "📱 Соцсети", "🛒 Расходники", "📚 Обучение", "🗂️ Прочее"}
    assert "🗂️ Прочее" in cats                                            # чужая категория превратилась в «Прочее»
    assert {i["priority"] for i in items} <= {"Срочно", "Важно", "Можно потом"}


def test_reminders_priority_outside_the_list_becomes_null(monkeypatch):
    async def fake_open(self, user_id=""):
        return [Work(id="1", title="TitleUnique456", priority="СекретнаяПриоритетность", deadline_str="", category_str="",
                     has_client=True, status="open", category="SecretCategoryXYZ", deadline_dt=at(0, 12),
                     reminder_dt=at(0, 9), notes="NoteUnique789", client_id="5")]
    monkeypatch.setattr(arcana_route, "get_user_id", AsyncMock(return_value="u"))
    monkeypatch.setattr(arcana_route, "today_user_tz", AsyncMock(return_value=(TODAY, 3)))
    monkeypatch.setattr(type(arcana_route._pg_works_repo), "list_open", fake_open)
    r = reminders(7)
    assert r.status_code == 200 and r.json()["items"] == [
        {"at": "2026-11-12T09:00:00+03:00", "kind": "work", "category": "🗂️ Прочее", "priority": None, "overdue": False}]
    assert "SecretCategoryXYZ" not in r.text and "СекретнаяПриоритетность" not in r.text and "TitleUnique456" not in r.text


def test_reminders_done_and_archived_are_not_returned_and_overdue_are(arcana_db):
    items = reminders(31).json()["items"]
    assert len(items) == 11                                               # 15 минус done, archived, no_dates, other_owner
    overdue = [i for i in items if i["overdue"]]
    assert len(overdue) == 2                                              # вчерашний срок и срок 20 дней назад


def test_reminders_overdue_follows_the_calendar_day_like_the_works_list(arcana_db):
    items = reminders(0).json()["items"]
    by_at = {i["at"]: i for i in items}
    today_morning = by_at["2026-11-12T07:00:00+03:00"]                    # срок сегодня 07:00, сейчас позже: ещё не просрочено
    assert today_morning["overdue"] is False
    yesterday = by_at["2026-11-11T23:59:00+03:00"]                        # срок вчера поздно вечером: просрочено
    assert yesterday["overdue"] is True


def test_reminders_window_edges_exactly(arcana_db):
    def days_of(days):
        return sorted((datetime.fromisoformat(i["at"]).date() - TODAY).days for i in reminders(days).json()["items"]
                      if not i["overdue"])
    assert days_of(0) == [0, 0, 0]                                        # today, today_deadline_only, today_past_hour
    assert days_of(7)[-1] == 7 and 8 not in days_of(7)                    # in7 входит, in8 нет
    assert 8 in days_of(8) and 31 not in days_of(8)
    assert days_of(31)[-1] == 31 and 32 not in days_of(31)                # in31 входит, in32 нет даже при days=31


def test_reminders_items_are_sorted_by_time(arcana_db):
    ats = [i["at"] for i in reminders(31).json()["items"]]
    assert ats == sorted(ats)


@pytest.mark.parametrize("bad", [-1, 32, 100])
def test_reminders_days_outside_0_31_is_422(arcana_db, bad):
    assert reminders(bad).status_code == 422


def test_reminders_without_a_user_id_returns_nothing_not_every_users_works(monkeypatch, arcana_db):
    monkeypatch.setattr(arcana_route, "get_user_id", AsyncMock(return_value=None))      # list_open("") не фильтрует по владельцу
    r = reminders(31)
    assert r.status_code == 200 and r.json() == {"days": 31, "items": []}


def test_reminders_valid_bearer_runs_as_the_configured_owner(env, arcana_db):
    assert reminders(7).status_code == 200
    seen = arcana_route.get_user_id.await_args_list[-1].args[0]
    assert seen == OWNER


def test_reminders_without_authorization_normal_login_still_works(arcana_db):
    c = api()
    assert c.get("/api/arcana/reminders").status_code == 401                         # нет initData и cookie
    r = c.get("/api/arcana/reminders", params={"days": 7}, headers={"X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert r.status_code == 200 and r.json()["items"]
    cookie, _ = issue_session(OTHER_OWNER, secret=SECRET, remember=False)
    assert api().get("/api/arcana/reminders", cookies={"hl_session": cookie}).status_code == 200
    r = api().get("/api/arcana/reminders", headers={"Authorization": "tma x", "X-Telegram-Init-Data": init_data(OTHER_OWNER)})
    assert r.status_code == 200                                                      # другая схема: обычный вход


@pytest.mark.parametrize("header", ["Bearer wrong-token", "Bearer", "Bearer " + TOKEN[:-1], "Bearer " + TOKEN + "x"])
def test_reminders_bad_bearer_is_401(header, arcana_db):
    r = api().get("/api/arcana/reminders", headers={"Authorization": header})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert r.json() == {"detail": "invalid token"}                                    # ничего из Arcana в ответе


def test_reminders_rate_limit_is_429_after_ten_bad_attempts(monkeypatch, arcana_db):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    c = api()
    bad = {"Authorization": "Bearer wrong"}
    assert [c.get("/api/arcana/reminders", headers=bad).status_code for _ in range(10)] == [401] * 10
    assert c.get("/api/arcana/reminders", headers=bad).status_code == 429
    assert c.get("/api/arcana/reminders", headers=OK).status_code == 429             # окно общее для обоих маршрутов шлюза
    assert c.get("/api/tasks", headers=OK).status_code == 429


@pytest.mark.parametrize("break_it", ["token", "owner", "allowed", "same_as_other"])
def test_reminders_gate_off_is_401(monkeypatch, arcana_db, break_it):
    if break_it == "token":
        monkeypatch.setattr(config, "mcp_read_token", "")
    elif break_it == "owner":
        monkeypatch.setattr(config, "mcp_read_owner_tg_id", 0)
    elif break_it == "allowed":
        monkeypatch.setattr(config, "allowed_ids", [OTHER_OWNER])
    else:
        monkeypatch.setattr(config, "cats_read_token", TOKEN)
    r = reminders(7)
    assert r.status_code == 401 and "TitleUnique456" not in r.text


def test_reminders_route_has_no_router_level_dependency_that_rejects_bearer():
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == "/api/arcana/reminders")
    assert route.methods == {"GET"}
    gates = [d for d in route.dependant.dependencies if d.call is gate.mcp_read_gate]
    assert len(gates) == 1                                                            # ровно один шлюз, чужих гейтов нет


# Маршруты, которые отвечают 200 всем, с токеном и без него (публичные страницы, документация, выход, публичный
# booking). Новый публичный маршрут нельзя добавить незаметно: тест упадёт, пока его не внесут сюда осознанно.
PUBLIC_ROUTES = {
    ("GET", "/"), ("GET", "/health"), ("GET", "/login"), ("GET", "/logout"), ("POST", "/logout"),
    ("GET", "/docs"), ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"), ("GET", "/openapi.json"),
    ("GET", "/api/booking/calendar"), ("GET", "/api/booking/me"), ("GET", "/api/booking/meeting-types"),
    ("GET", "/api/booking/public"), ("GET", "/api/booking/tip"),
}
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
OPEN_WITH_TOKEN = {("GET", "/api/tasks"), ("GET", "/api/arcana/reminders")}      # ровно два маршрута (ADR-0038)


def test_valid_bearer_opens_nothing_but_the_two_gated_gets_across_all_routes():
    """ГЛАВНЫЙ тест: обходим все маршруты приложения (app.routes) всеми методами с верным Bearer."""
    paths: dict[str, set[str]] = {}
    for r in app.routes:
        p, ms = getattr(r, "path", None), getattr(r, "methods", None)
        if p:                                                            # у Mount пути нет (статика SPA)
            paths.setdefault(p, set()).update(ms or [])
    assert len(paths) > 100                                              # обход не пустой
    for prefix in ("/api/finance", "/api/arcana", "/api/cats", "/api/cat-health", "/api/care", "/api/memory",
                   "/api/people", "/api/booking", "/api/lists", "/api/calendar"):
        assert any(p.startswith(prefix) for p in paths), prefix          # включая admin-, finance-, arcana-, cats-маршруты

    c = api(raise_server_exceptions=False)
    bad: list[str] = []
    calls = 0
    for path, declared in sorted(paths.items()):
        url = re.sub(r"\{[^}]*\}", "1", path)
        for method in sorted((set(declared) | set(ALL_METHODS)) - {"HEAD", "OPTIONS"}):
            kw = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            with_token = c.request(method, url, headers=OK, **kw)
            calls += 1
            key = (method, path)
            if key in OPEN_WITH_TOKEN:
                if with_token.status_code != 200:
                    bad.append(f"{method} {path} с токеном должен быть 200, а не {with_token.status_code}")
            elif key in PUBLIC_ROUTES:
                without = c.request(method, url, **kw)
                if with_token.status_code != without.status_code:        # токен не должен ничего менять и тут
                    bad.append(f"{method} {path}: с токеном {with_token.status_code}, без {without.status_code}")
            else:
                if with_token.status_code not in (401, 403, 404, 405, 422, 429):
                    bad.append(f"{method} {path} -> {with_token.status_code}")
                # Даже 422 (маршрут пропустил авторизацию и споткнулся о параметры) выдаёт открытый доступ:
                # без заголовка тот же запрос обязан кончаться так же (429 лимита неверных попыток = 401).
                without = c.request(method, url, **kw)
                norm = lambda code: 401 if code == 429 else code            # noqa: E731
                if norm(with_token.status_code) != norm(without.status_code):
                    bad.append(f"{method} {path}: с токеном {with_token.status_code}, без заголовка {without.status_code}")
            if with_token.status_code >= 500:
                bad.append(f"{method} {path} упал: {with_token.status_code}")
    assert calls > 500
    assert not bad, "Bearer MCP открыл лишнее:\n" + "\n".join(bad)
