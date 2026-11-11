"""tests/test_cats_read_safety.py — Cats read API, N2: враждебные данные в карточках кота.

API отдаёт JSON, а не HTML: текст — это данные, их экранирует потребитель при сборке HTML (тест на стороне
heylark_cats). Здесь проверяется то, за что отвечает Nexus: небезопасный photo_url отбрасывается (null), текст отдаётся
как есть и только как application/json (без угадывания типа), небезопасный адрес от хранилища не сохраняется.
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text

from core.cats import safe_photo_url
from core.config import config
from core.db import get_engine
from core.repos import pg_cats_repo as crepo
from core.repos.cats_tables import cats
from core.repos.memories_table import memories
from miniapp.backend import cats_read_auth as gate
from miniapp.backend.app import app
from miniapp.backend.auth import current_user_id

MARK = "test-cats-read-safety"
TOKEN = "test-only-token-" + "c3" * 12
OWNER = 700000125                                              # вымышленный tg_id
HDR = {"Authorization": f"Bearer {TOKEN}"}
GOOD = "https://res.cloudinary.com/demo-cloud/image/upload/v1/cats/example-1.jpg"

HOSTILE_URLS = [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "java\nscript:alert(1)",
    "data:text/html,<script>alert(1)</script>", "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "vbscript:msgbox(1)",
    "//evil.example/x.jpg", "/relative/path.jpg", "http://res.cloudinary.com/demo/x.jpg", "ftp://host/x.jpg",
    'https://res.cloudinary.com/x"onerror="alert(1)', "https://res.cloudinary.com/x'onerror='alert(1)",
    "https://res.cloudinary.com/x><script>alert(1)</script>", "https://res.cloudinary.com/x y.jpg",
    "https://res.cloudinary.com/x\\evil", "https://res.cloudinary.com/x`y", "https://user:secret@res.cloudinary.com/x.jpg",
    "https://", "https:///x.jpg", "https://res.cloudinary.com/" + "a" * 2100, "",
]
HOSTILE_TEXT = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "' onfocus='alert(1)", "javascript:alert(1)",
                "&lt;b&gt; &amp; <b>", "{{7*7}}", "</p><iframe src=//evil.example>"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(config, "cats_read_token", TOKEN)
    monkeypatch.setattr(config, "cats_read_owner_tg_id", OWNER)
    monkeypatch.setattr(config, "allowed_ids", [OWNER])
    monkeypatch.setattr(gate, "_limiter", gate.FailureLimiter())
    app.dependency_overrides.pop(current_user_id, None)
    with patch("miniapp.backend.routes.cats.get_user_id", AsyncMock(return_value=MARK)):
        yield
    with get_engine().begin() as c:
        c.execute(delete(memories).where(memories.c.user_id == MARK))
        c.execute(delete(cats).where(cats.c.user_id == MARK))


def _set_photo(cid, url):
    with get_engine().begin() as c:                                        # в БД может лежать что угодно (скрипты, ручная правка)
        c.execute(text("UPDATE cats SET photo_url = :u WHERE id = :i"), {"u": url, "i": cid})


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_hostile_photo_url_is_dropped_everywhere(url):
    cid = crepo.create(MARK, "Тучка")
    _set_photo(cid, url)
    c = TestClient(app)
    lst = c.get("/api/cats", headers=HDR).json()["cats"][0]
    lst_f = c.get("/api/cats", params={"with_facts": 1}, headers=HDR).json()["cats"][0]
    card = c.get(f"/api/cats/{cid}", headers=HDR).json()
    assert lst["photo_url"] is None and lst_f["photo_url"] is None and card["photo_url"] is None, repr(url)[:60]
    assert safe_photo_url(url) is None


@pytest.mark.parametrize("url", [GOOD, "HTTPS://res.cloudinary.com/demo/x.jpg", "https://cdn.example.org:8443/a/b.png?v=1&w=2",
                                 "https://res.cloudinary.com/demo/image/upload/c_fill,w_400/v1/cats/a.jpg"])
def test_good_photo_url_is_kept_verbatim(url):
    cid = crepo.create(MARK, "Тучка")
    _set_photo(cid, url)
    assert TestClient(app).get(f"/api/cats/{cid}", headers=HDR).json()["photo_url"] == url


def test_no_photo_stays_null():
    cid = crepo.create(MARK, "Тучка")
    assert TestClient(app).get(f"/api/cats/{cid}", headers=HDR).json()["photo_url"] is None


@pytest.mark.parametrize("payload", HOSTILE_TEXT)
def test_hostile_text_is_returned_as_json_data_only(payload):
    cid = crepo.create(MARK, payload[:60], [payload[:30]])
    with get_engine().begin() as c:
        c.execute(insert(memories).values(fact_text=payload, key_name="k1", category="🐾 Коты", user_id=MARK,
                                          review_status="confirmed", cat_id=cid, cat_block="care"))
    r = TestClient(app).get("/api/cats", params={"with_facts": 1}, headers=HDR)
    assert r.headers["content-type"].split(";")[0] == "application/json"       # браузер не примет за HTML
    assert r.headers["x-content-type-options"] == "nosniff"
    cat = r.json()["cats"][0]
    assert cat["name"] == payload[:60] and cat["aliases"] == [payload[:30]]
    assert cat["facts"][0]["items"][0]["text"] == payload                        # данные как есть: экранирует потребитель
    card = TestClient(app).get(f"/api/cats/{cid}", headers=HDR)
    assert card.headers["content-type"].split(";")[0] == "application/json" and card.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://res.cloudinary.com/x.jpg", 'https://x/"onerror="a', "data:text/html,hi"])
def test_unsafe_url_from_storage_is_not_saved(bad):
    cid = crepo.create(MARK, "Тучка")
    app.dependency_overrides[current_user_id] = lambda: OWNER                      # запись — обычный вход владельца
    try:
        with patch("miniapp.backend.routes.cats.cloudinary_upload", AsyncMock(return_value=bad)):
            r = TestClient(app).post(f"/api/cats/{cid}/photo", files={"file": ("a.jpg", io.BytesIO(b"x"), "image/jpeg")})
    finally:
        app.dependency_overrides.pop(current_user_id, None)
    assert r.status_code == 502
    assert crepo.get_cat(MARK, cid)["photo_url"] is None


def test_safe_url_from_storage_is_saved():
    cid = crepo.create(MARK, "Тучка")
    app.dependency_overrides[current_user_id] = lambda: OWNER
    try:
        with patch("miniapp.backend.routes.cats.cloudinary_upload", AsyncMock(return_value=GOOD)):
            r = TestClient(app).post(f"/api/cats/{cid}/photo", files={"file": ("a.jpg", io.BytesIO(b"x"), "image/jpeg")})
    finally:
        app.dependency_overrides.pop(current_user_id, None)
    assert r.status_code == 200 and crepo.get_cat(MARK, cid)["photo_url"] == GOOD


def test_control_characters_are_rejected_by_the_function():
    # NUL в текстовую колонку Postgres не попадёт, но функция обязана отбросить и его, и прочие управляющие символы
    for bad in ("https://res.cloudinary.com/x\x00.jpg", "https://res.cloudinary.com/x\x1f.jpg", "https://res.cloudinary.com/x\x7f.jpg",
                "https://res.cloudinary.com/x\r\n.jpg", "https://res.cloudinary.com/x\t.jpg"):
        assert safe_photo_url(bad) is None


def test_non_string_photo_values():
    for v in (None, 0, 1.5, [], {}, b"https://x/y.jpg"):
        assert safe_photo_url(v) is None
