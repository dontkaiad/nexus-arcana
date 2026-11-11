"""tests/test_cats_read_token.py — Cats read API, N1: служебный доступ к GET /api/cats по Bearer-токену (ADR-0033).

Реальная БД и реальный путь авторизации (без подмены current_user_id). Токен и tg_id владельца — вымышленные,
подставляются в config только на время теста. Имена вымышленные.
"""
from __future__ import annotations

import hmac
import logging
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert

from core.config import config, load_config
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from miniapp.backend import cats_read_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cats-read-token"
TOKEN = "test-only-token-" + "a1" * 12                        # вымышленный, 40 символов
OWNER = 700000123                                              # вымышленный tg_id
PUBLIC_PEER = ("8.8.8.8", 5000)                                # настоящий публичный адрес (документационные диапазоны Python считает приватными)
PROXY_PEER = ("172.18.0.5", 5000)                              # приватный адрес прокси в docker-сети
OK = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "cats_read_token", TOKEN)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER, 700000001])
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(cats).where(cats.c.user_id == MARK))


def api(peer=PUBLIC_PEER):
    """TestClient, у которого сервер видит peer-адрес (этот Starlette не умеет TestClient(client=…))."""
    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": peer}
        await app(scope, receive, send)
    return TestClient(with_peer)


@pytest.fixture
def one_cat():
    cid = crepo.create(MARK, "Тучка", ["Туча"])
    with get_engine().begin() as c:
        c.execute(insert(memories).values(fact_text="Тучка любит окно", key_name="k1", category="🐾 Коты",
                                          user_id=MARK, review_status="confirmed", cat_id=cid, cat_block="care"))
    return cid


# ── доступ по токену ─────────────────────────────────────────────────────────

def test_valid_bearer_reads_list_and_card(one_cat):
    r = api().get("/api/cats", headers=OK)
    assert r.status_code == 200 and [c["name"] for c in r.json()["cats"]] == ["Тучка"]
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    card = api().get(f"/api/cats/{one_cat}", headers=OK)
    assert card.status_code == 200 and card.json()["facts"][0]["items"][0]["text"] == "Тучка любит окно"


def test_scheme_is_case_insensitive(one_cat):
    assert api().get("/api/cats", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200


@pytest.mark.parametrize("header", [
    "Bearer wrong-token", "Bearer", "Bearer   ", f"Basic {TOKEN}", TOKEN, f"Token {TOKEN}", "Bearer " + TOKEN[:-1],
    "Bearer " + TOKEN + "x", "",
])
def test_bad_authorization_is_401(header):
    r = api().get("/api/cats", headers={"Authorization": header})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert api().get("/api/cats/1", headers={"Authorization": header}).status_code == 401


def test_without_authorization_header_normal_auth_applies():
    assert api().get("/api/cats").status_code == 401                        # нет initData / cookie
    app.dependency_overrides[current_user_id] = lambda: OWNER
    try:
        assert api().get("/api/cats").status_code == 200                    # путь initData / cookie не тронут
    finally:
        app.dependency_overrides.pop(current_user_id, None)


def test_bad_bearer_is_rejected_even_with_valid_normal_auth():
    app.dependency_overrides[current_user_id] = lambda: OWNER               # «вошёл» обычным способом
    try:
        assert api().get("/api/cats", headers={"Authorization": "Bearer wrong"}).status_code == 401
    finally:
        app.dependency_overrides.pop(current_user_id, None)


# ── выключено / неверно настроено ────────────────────────────────────────────

@pytest.mark.parametrize("token", ["", "short-token", "x" * 31])
def test_disabled_when_token_missing_or_too_short(monkeypatch, token):
    monkeypatch.setattr(config, "cats_read_token", token)
    for sent in (token, "", "wrong"):                                       # пустой токен не должен «совпасть» с пустым
        assert api().get("/api/cats", headers={"Authorization": f"Bearer {sent}"}).status_code == 401


def test_disabled_when_owner_not_configured_or_not_allowed(monkeypatch):
    monkeypatch.setattr(config, "cats_read_owner_tg_id", 0)
    assert api().get("/api/cats", headers=OK).status_code == 401
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [700000001])                  # владелец токена не в списке разрешённых
    assert api().get("/api/cats", headers=OK).status_code == 401


# ── токен читает ТОЛЬКО два GET ──────────────────────────────────────────────

def test_token_does_not_open_anything_else(one_cat):
    c = api()
    assert c.get("/api/cats/general", headers=OK).status_code == 401
    assert c.post("/api/cats", json={"name": "Х"}, headers=OK).status_code == 401
    assert c.patch(f"/api/cats/{one_cat}", json={"name": "Х"}, headers=OK).status_code == 401
    assert c.post(f"/api/cats/{one_cat}/facts", json={"text": "х"}, headers=OK).status_code == 401
    assert c.post(f"/api/cats/{one_cat}/facts/1/attach", headers=OK).status_code == 401
    assert c.post(f"/api/cats/{one_cat}/photo", files={"file": ("a.jpg", b"x", "image/jpeg")}, headers=OK).status_code == 401
    for path in ("/api/memory", "/api/people", "/api/tasks", "/api/finance/month"):
        assert c.get(path, headers=OK).status_code in (401, 404), path
    assert len(crepo.list_cats(MARK)) == 1 and crepo.list_cats(MARK)[0]["name"] == "Тучка"      # ничего не записалось


# ── hmac.compare_digest и логи ───────────────────────────────────────────────

def test_token_is_compared_with_hmac_compare_digest(one_cat):
    with patch.object(gate.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        assert api().get("/api/cats", headers=OK).status_code == 200
        assert api().get("/api/cats", headers={"Authorization": "Bearer wrong-token-value"}).status_code == 401
    assert spy.call_count == 2
    assert all(isinstance(a, bytes) for call in spy.call_args_list for a in call.args)


def test_token_value_never_reaches_logs(caplog, one_cat):
    caplog.set_level(logging.DEBUG)
    bad = "Bearer " + "q" * 40
    api().get("/api/cats", headers=OK)
    api().get("/api/cats", headers={"Authorization": bad})
    assert TOKEN not in caplog.text and ("q" * 40) not in caplog.text
    assert "bad bearer" in caplog.text                                       # сама попытка залогирована, без значения


# ── лимит неверных попыток ───────────────────────────────────────────────────

def _fake_clock():
    t = {"now": 1000.0}
    return t, (lambda: t["now"])


def test_rate_limit_blocks_after_ten_bad_attempts_even_for_valid_token(monkeypatch, one_cat):
    t, clock = _fake_clock()
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=clock))
    bad = {"Authorization": "Bearer wrong"}
    c = api()
    assert [c.get("/api/cats", headers=bad).status_code for _ in range(10)] == [401] * 10
    r = c.get("/api/cats", headers=bad)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60
    assert c.get("/api/cats", headers=OK).status_code == 429                 # пока окно не освободилось — и верному тоже
    t["now"] += 61
    assert c.get("/api/cats", headers=OK).status_code == 200


def test_valid_requests_are_never_counted(monkeypatch, one_cat):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    c = api()
    assert all(c.get("/api/cats", headers=OK).status_code == 200 for _ in range(40))


def test_failures_expire_with_sliding_window(monkeypatch, one_cat):
    t, clock = _fake_clock()
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=clock))
    c = api()
    for _ in range(9):
        assert c.get("/api/cats", headers={"Authorization": "Bearer w"}).status_code == 401
    t["now"] += 61
    for _ in range(9):                                                       # старые девять сгорели — снова можно 9
        assert c.get("/api/cats", headers={"Authorization": "Bearer w"}).status_code == 401
    assert c.get("/api/cats", headers=OK).status_code == 200


def test_limit_is_per_ip(monkeypatch, one_cat):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    attacker, friend = api(("8.8.8.8", 1)), api(("8.8.4.4", 1))
    for _ in range(11):
        attacker.get("/api/cats", headers={"Authorization": "Bearer w"})
    assert attacker.get("/api/cats", headers=OK).status_code == 429
    assert friend.get("/api/cats", headers=OK).status_code == 200


def test_behind_proxy_the_real_client_is_the_last_forwarded_address(monkeypatch, one_cat):
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter(clock=_fake_clock()[1]))
    proxy = api(PROXY_PEER)
    spoof = {"Authorization": "Bearer w", "X-Forwarded-For": "1.2.3.4, 198.51.100.1"}     # клиент подсунул первый адрес
    for _ in range(11):
        proxy.get("/api/cats", headers=spoof)
    blocked = proxy.get("/api/cats", headers={**OK, "X-Forwarded-For": "198.51.100.1"})
    other = proxy.get("/api/cats", headers={**OK, "X-Forwarded-For": "198.51.100.2"})
    assert blocked.status_code == 429 and other.status_code == 200


def test_client_ip_rules():
    assert gate.client_ip("8.8.8.8", "9.9.9.9") == "8.8.8.8"                  # публичный peer: заголовку не верим
    assert gate.client_ip("172.18.0.5", "9.9.9.9, 198.51.100.1") == "198.51.100.1"
    assert gate.client_ip("127.0.0.1", "2001:db8::1") == "2001:db8::1"
    assert gate.client_ip("172.18.0.5", "not-an-ip") == "172.18.0.5"
    assert gate.client_ip("172.18.0.5", None) == "172.18.0.5"
    assert gate.client_ip("testclient", "9.9.9.9") == "testclient" and gate.client_ip(None, None) == "unknown"


def test_limiter_memory_is_bounded():
    t, clock = _fake_clock()
    lim = gate.FailureLimiter(clock=clock, max_tracked=50)
    for i in range(500):
        lim.record_failure(f"10.0.0.{i}")
        t["now"] += 0.001
    assert len(lim._hits) <= 50
    lim.reset()
    assert lim.retry_after("10.0.0.1") == 0


# ── конфиг ───────────────────────────────────────────────────────────────────

def test_config_reads_token_and_owner_from_env():
    base = {"NEXUS_BOT_TOKEN": "1:test", "ANTHROPIC_API_KEY": "k", "ALLOWED_TELEGRAM_IDS": "700000001"}
    with patch.dict(os.environ, {**base, "CATS_READ_TOKEN": f"  {TOKEN}  ", "CATS_READ_OWNER_TG_ID": str(OWNER)}):
        c = load_config()
    assert c.cats_read_token == TOKEN and c.cats_read_owner_tg_id == OWNER
    for bad in ("", "abc", " "):
        with patch.dict(os.environ, {**base, "CATS_READ_OWNER_TG_ID": bad}):
            assert load_config().cats_read_owner_tg_id == 0                  # мусор → выключено, без падения
    with patch.dict(os.environ, {**base}, clear=False):
        os.environ.pop("CATS_READ_TOKEN", None)
        assert load_config().cats_read_token == ""


def test_env_example_has_no_values():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    lines = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in text.splitlines() if l.startswith("CATS_READ_")}
    assert lines == {"CATS_READ_TOKEN": "", "CATS_READ_OWNER_TG_ID": ""}
