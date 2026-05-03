"""tests/test_cors_config.py — CORS конфиг miniapp/backend/app.py:

- старого «*» больше нет
- дефолт содержит telegram-домены и localhost
- env MINIAPP_CORS_ORIGINS перекрывает дефолт
- regex для *.trycloudflare.com валидный
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
APP_PATH = REPO / "miniapp" / "backend" / "app.py"


def test_no_wildcard_origin_in_source():
    src = APP_PATH.read_text(encoding="utf-8")
    assert 'allow_origins=["*"]' not in src, (
        "В app.py остался allow_origins=['*'] — небезопасно для CORS"
    )
    # И комментария-TODO про сужение тоже быть не должно
    assert "TODO: сузить до домена mini app" not in src


def test_default_origins_include_telegram_and_localhost():
    """Импортируем модуль БЕЗ env override → дефолт активен."""
    import miniapp.backend.app as app_mod
    importlib.reload(app_mod)
    origins = app_mod.allowed_origins
    for must in (
        "https://web.telegram.org",
        "https://webk.telegram.org",
        "https://webz.telegram.org",
        "https://t.me",
        "http://localhost:5173",
    ):
        assert must in origins, f"дефолт CORS не содержит {must}"


def test_env_override_replaces_defaults(monkeypatch):
    monkeypatch.setenv(
        "MINIAPP_CORS_ORIGINS",
        "https://mybot.example.com, https://staging.example.com",
    )
    import miniapp.backend.app as app_mod
    importlib.reload(app_mod)
    origins = app_mod.allowed_origins
    assert origins == [
        "https://mybot.example.com",
        "https://staging.example.com",
    ]
    # Дефолтных telegram-доменов больше нет — env полностью переопределил.
    assert "https://web.telegram.org" not in origins


def test_env_empty_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("MINIAPP_CORS_ORIGINS", "   ")  # пробелы = пусто
    import miniapp.backend.app as app_mod
    importlib.reload(app_mod)
    assert "https://web.telegram.org" in app_mod.allowed_origins


def test_cloudflare_tunnel_regex_compiles_and_matches():
    """Источник содержит regex для *.trycloudflare.com и он валидный."""
    src = APP_PATH.read_text(encoding="utf-8")
    assert "trycloudflare" in src  # экранирование точки не влияет
    pattern = r"^https://.*\.trycloudflare\.com$"
    rx = re.compile(pattern)
    assert rx.match("https://abc-def.trycloudflare.com")
    assert rx.match("https://my-tunnel-xyz.trycloudflare.com")
    assert not rx.match("http://abc.trycloudflare.com"),  "только https"
    assert not rx.match("https://trycloudflare.com")  # без поддомена


def test_credentials_and_methods_configured():
    """allow_credentials=True (для Telegram WebApp init data) +
    allow_methods=['*'] (PATCH/DELETE/...)."""
    src = APP_PATH.read_text(encoding="utf-8")
    assert "allow_credentials=True" in src
    assert 'allow_methods=["*"]' in src


# Восстановить дефолт после suite — следующие тесты в pytest сессии не
# должны видеть подмененный allowed_origins.
@pytest.fixture(autouse=True)
def _reload_app_module_after_test():
    yield
    import miniapp.backend.app as app_mod
    importlib.reload(app_mod)
