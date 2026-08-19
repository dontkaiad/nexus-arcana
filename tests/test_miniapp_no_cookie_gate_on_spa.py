"""tests/test_miniapp_no_cookie_gate_on_spa.py — the Mini App shell must
never require an hl_session cookie to load.

Regression: a page-level `require_owner_session` middleware was briefly
added to gate every non-/api request behind the hl_session cookie (falling
back to a 307 to /login → login.heylark.dev otherwise). It broke every real
Telegram Mini App session: Telegram never sends hl_session on the top-level
HTML request for /nexus or /arcana — only initData, and only as a header on
/api/* calls, injected by Telegram's own JS bridge after the page's JS runs.
So every genuine Mini App load got redirected straight to the external
login page — whose Telegram Login Widget doesn't even render inside
Telegram's own WebView (nested iframe), a dead end for every user.

/api/* already enforces its own auth (cookie OR initData, see
miniapp/backend/auth.py::current_user_id) — the SPA shell itself carries no
data, so it's fine to serve unauthenticated. This test locks in "no page
gate", requiring dist/ to exist (built by the frontend build step) so the
static mount is actually active.
"""
from __future__ import annotations

import pathlib

import pytest

import tests.conftest  # noqa: F401 — sets required env vars for config import

from fastapi.testclient import TestClient

from miniapp.backend.app import app

_DIST = pathlib.Path(__file__).parent.parent / "miniapp" / "frontend" / "dist"

pytestmark = pytest.mark.skipif(
    not _DIST.is_dir(), reason="frontend/dist not built — run `npm run build` in miniapp/frontend first"
)

client = TestClient(app, follow_redirects=False)


def test_nexus_spa_loads_without_any_cookie():
    r = client.get("/nexus")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/html")


def test_arcana_spa_loads_without_any_cookie():
    r = client.get("/arcana")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/html")


def test_spa_root_loads_without_any_cookie():
    r = client.get("/")
    assert r.status_code == 200


def test_spa_does_not_redirect_to_login():
    for path in ("/nexus", "/arcana", "/"):
        r = client.get(path)
        assert r.status_code != 307, f"{path} must not redirect (got Location: {r.headers.get('location')})"
