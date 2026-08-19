"""tests/test_login_page_webview_bounce.py — /login must never trap a real
Telegram Mini App session.

/login is browser-only SSO (Telegram Login Widget + hl_session cookie). The
widget iframe can't work inside Telegram's own WebView, so if this page is
ever reached while a Mini App session is already live (window.Telegram.
WebApp.initData present — e.g. a stale menu-button URL cache, or a shared
link tapped inside Telegram), the page must bounce straight into the real
app via an early inline script, before the (broken, in that context) widget
even loads.
"""
from __future__ import annotations

import tests.conftest  # noqa: F401 — sets required env vars for config import

from fastapi.testclient import TestClient

from miniapp.backend.app import app

client = TestClient(app)


def test_login_bounces_to_nexus_by_default():
    r = client.get("/login")
    assert r.status_code == 200
    assert 'location.replace("/nexus")' in r.text


def test_login_bounces_to_safe_next_path():
    r = client.get("/login", params={"next": "/tasks"})
    assert r.status_code == 200
    assert 'location.replace("/tasks")' in r.text


def test_login_ignores_unsafe_next_and_falls_back_to_nexus():
    r = client.get("/login", params={"next": "https://evil.example.com"})
    assert r.status_code == 200
    assert 'location.replace("/nexus")' in r.text
    assert "evil.example.com" not in r.text.split("location.replace")[1][:60]


def test_login_bounce_script_checks_initdata_before_replacing():
    r = client.get("/login")
    body = r.text
    idx = body.find("Telegram.WebApp.initData")
    assert idx != -1, "bounce guard must check window.Telegram.WebApp.initData"
    assert body.find("location.replace", idx) > idx, "replace must come after the initData check"
