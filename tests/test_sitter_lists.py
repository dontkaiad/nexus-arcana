"""tests/test_sitter_lists.py — списки «для ситтера»: nexus_lists.for_sitter + GET /api/sitter/lists + отдельный токен.

Реальная БД и реальный путь авторизации. Токены и tg_id вымышленные, подставляются в config только на время теста.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, inspect, select, text

from core.config import config
from core.db import get_engine
from core.repos.lists_table import nexus_lists as nl
from miniapp.backend import cats_read_auth as cats_gate
from miniapp.backend import sitter_lists_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

REPO = Path(__file__).resolve().parent.parent
MARK, MARK2 = "test-sitter-lists", "test-sitter-lists-other"
TOKEN = "test-only-sitter-" + "b2" * 12                       # вымышленный, 40 символов
CATS_TOKEN = "test-only-cats-" + "c3" * 12
OWNER = 700000123
PEER = ("8.8.8.8", 5000)
OK = {"Authorization": f"Bearer {TOKEN}"}
SECRET_FIELDS = {"id", "user_id", "price_actual", "price_plan", "store", "task_id", "works_id", "shared", "priority",
                 "category", "created_at", "updated_at", "for_sitter", "remind_days", "expires_at"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "sitter_lists_token", TOKEN)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "cats_read_token", CATS_TOKEN)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER, 700000001])
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    monkeypatch.setattr(cats_gate, "_limiter", cats_gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=MARK)):
        yield
    with get_engine().begin() as c:
        c.execute(delete(nl).where(nl.c.user_id.in_([MARK, MARK2])))


def api(peer=PEER):
    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": peer}
        await app(scope, receive, send)
    return TestClient(with_peer)


def mk(name="Миска в шкафу", user=MARK, **kw) -> int:
    vals = dict(name=name, user_id=user, for_sitter=True, group_name="Кухня")
    vals.update(kw)
    with get_engine().begin() as c:
        return c.execute(insert(nl).values(**vals).returning(nl.c.id)).scalar()


# ── Миграция ──────────────────────────────────────────────────────────────────

def _load_migration():
    path = next((REPO / "alembic" / "versions").glob("*_nexus_lists_for_sitter.py"))
    spec = importlib.util.spec_from_file_location("_mig_sitter", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def test_migration_chain_column_default_closed_and_partial_index():
    assert _load_migration().down_revision == "e6c9a2d4b831"
    cols = {c["name"]: c for c in inspect(get_engine()).get_columns("nexus_lists")}
    assert cols["for_sitter"]["nullable"] is False
    with get_engine().begin() as c:
        tid = c.execute(insert(nl).values(name="Обычный пункт", user_id=MARK).returning(nl.c.id)).scalar()
        assert c.execute(select(nl.c.for_sitter).where(nl.c.id == tid)).scalar() is False        # по умолчанию закрыт
        d = c.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_nexus_lists_for_sitter'")).scalar()
    assert "WHERE for_sitter" in d


def test_migration_up_down_reversible_keeps_rows():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = _load_migration()
    iid = mk()
    with get_engine().begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
            assert "for_sitter" not in {c["name"] for c in inspect(conn).get_columns("nexus_lists")}
            assert conn.execute(text("SELECT count(*) FROM nexus_lists WHERE id = :i"), {"i": iid}).scalar() == 1
            mig.upgrade()
        assert conn.execute(text("SELECT for_sitter FROM nexus_lists WHERE id = :i"), {"i": iid}).scalar() is False


# ── Шлюз и токены ─────────────────────────────────────────────────────────────

def test_valid_token_returns_200_no_store_nosniff():
    mk()
    r = api().get("/api/sitter/lists", headers=OK)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": ""}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "},
    {"Authorization": "Basic " + TOKEN}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Bearer {TOKEN}x"},
    {"Authorization": f"Bearer {TOKEN[:-1]}"}, {"Authorization": TOKEN},
])
def test_missing_or_bad_credentials_are_401_without_fallthrough(headers):
    mk()
    r = api().get("/api/sitter/lists", headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert "Кухня" not in r.text


def test_no_fallthrough_to_owner_login_even_with_valid_initdata_override():
    """Владельческий вход (initData/cookie) этот маршрут не открывает: только токен ситтера."""
    mk()
    app.dependency_overrides[current_user_id] = lambda: OWNER
    try:
        assert api().get("/api/sitter/lists").status_code == 401
    finally:
        app.dependency_overrides.pop(current_user_id, None)


def test_cats_token_does_not_open_sitter_lists():
    mk()
    assert api().get("/api/sitter/lists", headers={"Authorization": f"Bearer {CATS_TOKEN}"}).status_code == 401


@pytest.mark.parametrize("path", ["/api/cats", "/api/cats/1", "/api/lists", "/api/tasks", "/api/memory", "/api/today"])
def test_sitter_token_opens_nothing_else(path):
    r = api().get(path, headers=OK)
    assert r.status_code in (401, 403, 404, 422) and r.status_code != 200


def test_post_flag_endpoint_rejects_sitter_token():
    iid = mk(for_sitter=False)
    r = api().post(f"/api/lists/{iid}/sitter", json={"for_sitter": True}, headers=OK)
    assert r.status_code in (401, 403)
    with get_engine().connect() as c:
        assert c.execute(select(nl.c.for_sitter).where(nl.c.id == iid)).scalar() is False


@pytest.mark.parametrize("tok,owner,cats_tok,allowed", [
    ("", OWNER, CATS_TOKEN, [OWNER]), ("short", OWNER, CATS_TOKEN, [OWNER]), (TOKEN, 0, CATS_TOKEN, [OWNER]),
    (TOKEN, OWNER, CATS_TOKEN, [700000001]), (TOKEN, OWNER, TOKEN, [OWNER]),         # токены совпали → выключено
])
def test_feature_off_unless_configured_and_tokens_differ(monkeypatch, tok, owner, cats_tok, allowed):
    mk()
    monkeypatch.setattr(config, "sitter_lists_token", tok)
    monkeypatch.setattr(config, "sitter_lists_owner_tg_id", owner)
    monkeypatch.setattr(config, "cats_read_token", cats_tok)
    monkeypatch.setattr(config, "allowed_ids", allowed)
    assert api().get("/api/sitter/lists", headers={"Authorization": f"Bearer {tok or 'x'}"}).status_code == 401


def test_rate_limit_after_ten_failures_and_independent_from_cats_gate():
    mk()
    c = api()
    for _ in range(10):
        assert c.get("/api/sitter/lists", headers={"Authorization": "Bearer bad"}).status_code == 401
    r = c.get("/api/sitter/lists", headers=OK)                                  # верный токен с того же IP тоже в блоке
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert api(("8.8.4.4", 1)).get("/api/sitter/lists", headers=OK).status_code == 200   # другой IP не задет
    # счётчики токена котов не смешиваются: сто неверных попыток к /api/cats не блокируют ситтера
    for _ in range(11):
        api(("1.1.1.1", 1)).get("/api/cats", headers={"Authorization": "Bearer bad"})
    assert api(("1.1.1.1", 1)).get("/api/sitter/lists", headers=OK).status_code == 200


def test_token_never_logged(caplog):
    caplog.set_level("DEBUG")
    api().get("/api/sitter/lists", headers={"Authorization": f"Bearer {TOKEN}x"})
    api().get("/api/sitter/lists", headers=OK)
    assert TOKEN not in caplog.text


# ── Что отдаётся ──────────────────────────────────────────────────────────────

def test_only_flagged_own_non_archived_items_are_returned():
    mk("Помеченный", group_name="Кухня")
    mk("Не помеченный", for_sitter=False)
    mk("Чужой помеченный", user=MARK2)
    mk("Архивный", status="archived")
    body = api().get("/api/sitter/lists", headers=OK).json()
    names = [i["name"] for l in body["lists"] for i in l["items"]]
    assert names == ["Помеченный"] and body["count"] == 1


def test_response_has_exactly_allowed_fields_no_ids_prices_or_internal_refs():
    mk("Совок", note="синий, в ванной", quantity=2, price_actual=999, price_plan=888, store="Магазин-секрет",
       task_id="TASK-SECRET", works_id="WORK-SECRET", priority="срочно", category="Дом", shared=True, status="done")
    r = api().get("/api/sitter/lists", headers=OK)
    body = r.json()
    assert set(body) == {"lists", "count", "inventory", "inventory_count"}
    item = body["lists"][0]["items"][0]
    assert set(item) == {"name", "note", "quantity", "done", "kind"} and item["done"] is True and item["quantity"] == 2.0
    assert set(body["lists"][0]) == {"name", "items"}
    flat = json.dumps(body, ensure_ascii=False)
    for secret in ("999", "888", "Магазин-секрет", "TASK-SECRET", "WORK-SECRET", MARK):
        assert secret not in flat
    assert SECRET_FIELDS.isdisjoint(item)


def test_items_grouped_by_group_name_in_stable_order():
    mk("Б", group_name="Ванная"); mk("А", group_name="Кухня"); mk("В", group_name="Ванная")
    body = api().get("/api/sitter/lists", headers=OK).json()
    assert [(l["name"], [i["name"] for i in l["items"]]) for l in body["lists"]] == [("Ванная", ["Б", "В"]), ("Кухня", ["А"])]


def test_empty_when_nothing_flagged_and_when_owner_unknown(monkeypatch):
    mk(for_sitter=False)
    empty = {"lists": [], "count": 0, "inventory": [], "inventory_count": 0}
    assert api().get("/api/sitter/lists", headers=OK).json() == empty
    with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=None)):
        assert api().get("/api/sitter/lists", headers=OK).json() == empty


def test_no_access_to_memory_or_other_lists_in_payload():
    """Маршрут читает только nexus_lists: запись памяти с тем же владельцем в ответ не попадает."""
    from core.repos.memories_table import memories
    mk("Пункт")
    with get_engine().begin() as c:
        c.execute(insert(memories).values(fact_text="ПАМЯТЬ-СЕКРЕТ", user_id=MARK))
    try:
        assert "ПАМЯТЬ-СЕКРЕТ" not in api().get("/api/sitter/lists", headers=OK).text
    finally:
        with get_engine().begin() as c:
            c.execute(delete(memories).where(memories.c.user_id == MARK))


# ── XSS и чистка ──────────────────────────────────────────────────────────────

XSS = '<img src=x onerror=alert(1)><script>alert(2)</script>"\'&'


def test_html_in_texts_stays_inert_json_string_with_nosniff():
    """API отдаёт текст как JSON-строку (экранирует клиент): тип application/json + nosniff, разметка не интерпретируется."""
    mk(XSS, note=XSS, group_name=XSS)
    r = api().get("/api/sitter/lists", headers=OK)
    assert r.headers["content-type"].startswith("application/json") and r.headers["x-content-type-options"] == "nosniff"
    body = r.json()
    assert body["lists"][0]["items"][0]["name"] == XSS and body["lists"][0]["name"] == XSS         # данные не искажены


def test_lengths_capped_and_control_chars_removed():
    mk("н" * 500, note="з" * 900 + "\x1b\x07", group_name="г" * 200)
    body = api().get("/api/sitter/lists", headers=OK).json()
    item = body["lists"][0]["items"][0]
    assert len(item["name"]) == 120 and len(item["note"]) == 300 and len(body["lists"][0]["name"]) == 60
    mk("с\x01к\x1bрытое", note="a\x07b")
    flat = api().get("/api/sitter/lists", headers=OK).text
    assert "\\u0001" not in flat and "\\u001b" not in flat and "\\u0007" not in flat


# ── Пометка владельцем ────────────────────────────────────────────────────────

def _owner_client():
    app.dependency_overrides[current_user_id] = lambda: OWNER
    return api()


def test_owner_can_set_and_clear_flag():
    iid = mk(for_sitter=False)
    try:
        with patch("miniapp.backend.routes.sitter.get_user_id", AsyncMock(return_value=MARK)):
            c = _owner_client()
            assert c.post(f"/api/lists/{iid}/sitter", json={"for_sitter": True}).json() == {"ok": True, "for_sitter": True}
            assert [i["name"] for l in api().get("/api/sitter/lists", headers=OK).json()["lists"] for i in l["items"]] == ["Миска в шкафу"]
            assert c.post(f"/api/lists/{iid}/sitter", json={"for_sitter": False}).status_code == 200
            assert api().get("/api/sitter/lists", headers=OK).json()["count"] == 0
    finally:
        app.dependency_overrides.pop(current_user_id, None)


def test_owner_cannot_flag_foreign_or_missing_item():
    foreign = mk(user=MARK2, for_sitter=False)
    try:
        c = _owner_client()
        assert c.post(f"/api/lists/{foreign}/sitter", json={"for_sitter": True}).status_code == 404
        assert c.post("/api/lists/999999999/sitter", json={"for_sitter": True}).status_code == 404
        with get_engine().connect() as conn:
            assert conn.execute(select(nl.c.for_sitter).where(nl.c.id == foreign)).scalar() is False
    finally:
        app.dependency_overrides.pop(current_user_id, None)
