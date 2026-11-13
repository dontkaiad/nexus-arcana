"""tests/test_mcp_read_token.py — служебное чтение GET /api/tasks по Bearer-токену для kai-mcp (по образцу ADR-0033).

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
from datetime import date
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select

from core.config import config
from core.db import get_engine
from miniapp.backend import (care_period_auth, care_write_auth, cats_read_auth, cats_write_auth, mcp_read_auth as gate,
                             sitter_lists_auth)
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id
from miniapp.backend.routes import tasks as tasks_route
from miniapp.backend.tg_auth import issue_session
from nexus.repos import pg_tasks_repo
from nexus.repos.pg_tasks_repo import PgTasksRepo, Task
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
    for mod in (gate, cats_read_auth, sitter_lists_auth, care_write_auth, care_period_auth, cats_write_auth):
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


def test_the_gate_is_attached_to_exactly_one_route():
    assert _gated_routes() == [("GET", "/api/tasks")]


def test_writes_on_tasks_and_neighbours_stay_closed_with_the_token():
    c = api()
    for method, path, kw in (("POST", "/api/tasks", {"json": {"title": "x"}}), ("PATCH", "/api/tasks/1", {"json": {}}),
                             ("DELETE", "/api/tasks/1", {}), ("POST", "/api/tasks/1/done", {}),
                             ("POST", "/api/memory", {"json": {}}), ("GET", "/api/finance/month", {}),
                             ("GET", "/api/memory", {}), ("GET", "/api/people", {})):
        assert c.request(method, path, headers=OK, **kw).status_code in (401, 403, 404, 405, 422), (method, path)


# Маршруты, которые отвечают 200 всем, с токеном и без него (публичные страницы, документация, выход, публичный
# booking). Новый публичный маршрут нельзя добавить незаметно: тест упадёт, пока его не внесут сюда осознанно.
PUBLIC_ROUTES = {
    ("GET", "/"), ("GET", "/health"), ("GET", "/login"), ("GET", "/logout"), ("POST", "/logout"),
    ("GET", "/docs"), ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"), ("GET", "/openapi.json"),
    ("GET", "/api/booking/calendar"), ("GET", "/api/booking/me"), ("GET", "/api/booking/meeting-types"),
    ("GET", "/api/booking/public"), ("GET", "/api/booking/tip"),
}
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def test_valid_bearer_opens_nothing_but_get_api_tasks_across_all_routes():
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
            if key == ("GET", "/api/tasks"):
                if with_token.status_code != 200:
                    bad.append(f"GET /api/tasks с токеном должен быть 200, а не {with_token.status_code}")
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
