"""tests/test_spa_fallback.py — SPAStaticFiles SPA-fallback.

Проверяет:
- GET /nexus → 200 text/html (index.html, SPA-fallback)
- GET /arcana → 200 text/html (SPA-fallback)
- GET /       → 200 text/html (корень отдаёт index.html)
- GET /api/nonexistent → 404 (API-роутер выигрывает у статики)

Использует tmp-папку с index.html — не зависит от реального npm build.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def spa_client(tmp_path):
    """TestClient с SPAStaticFiles, смонтированными поверх заглушки dist."""
    # Создаём минимальный dist: только index.html
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")

    from miniapp.backend.app import SPAStaticFiles

    test_app = FastAPI()

    @test_app.get("/api/existing")
    async def existing():
        return {"ok": True}

    test_app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="spa")
    return TestClient(test_app, raise_server_exceptions=True)


def test_root_serves_index(spa_client):
    """GET / → index.html (200)."""
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "SPA" in r.text


def test_nexus_path_serves_index(spa_client):
    """GET /nexus → SPA fallback, index.html (200)."""
    r = spa_client.get("/nexus")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "SPA" in r.text


def test_arcana_path_serves_index(spa_client):
    """GET /arcana → SPA fallback, index.html (200)."""
    r = spa_client.get("/arcana")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "SPA" in r.text


def test_unknown_deep_path_serves_index(spa_client):
    """GET /some/deep/path → SPA fallback (200), не 404."""
    r = spa_client.get("/some/deep/path")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_api_existing_not_intercepted(spa_client):
    """GET /api/existing → роутер выигрывает (200 JSON), не статика."""
    r = spa_client.get("/api/existing")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_api_nonexistent_returns_404(spa_client):
    """GET /api/nonexistent → 404, не SPA-fallback.

    Starlette передаёт path без ведущего "/": path="api/nonexistent".
    SPAStaticFiles проверяет path.startswith("api/") → пробрасывает 404
    вместо index.html.
    """
    r = spa_client.get("/api/nonexistent")
    assert r.status_code == 404
    # SPA-fallback не сработал — не HTML
    assert "SPA" not in r.text
