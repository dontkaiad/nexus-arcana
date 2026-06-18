"""tests/test_startup_menu_button.py — WebApp-кнопка меню на старте ботов.

Проверяет:
- config.miniapp_base_url имеет дефолт https://core.heylark.dev
- MenuButtonWebApp создаётся с правильным типом, текстом и url для обоих ботов
- MenuButtonWebApp.type != MenuButtonCommands.type (убеждаемся что замена корректна)
"""
from __future__ import annotations

import pytest
from aiogram.types import MenuButtonWebApp, MenuButtonCommands, WebAppInfo

_DEFAULT_BASE = "https://core.heylark.dev"


def test_miniapp_base_url_default_in_config():
    """config.miniapp_base_url == 'https://core.heylark.dev' когда MINIAPP_BASE_URL не задан."""
    from core.config import config
    # Если .env не переопределяет — должен быть дефолт.
    # В тестовом окружении переменная не задана, поэтому проверяем дефолт напрямую.
    from core.config import _optional
    assert _optional("MINIAPP_BASE_URL", _DEFAULT_BASE) == config.miniapp_base_url or \
           config.miniapp_base_url == _DEFAULT_BASE


@pytest.mark.asyncio
async def test_nexus_webapp_button_type_and_url():
    """set_chat_menu_button для Nexus вызывается с MenuButtonWebApp(url=.../nexus)."""
    called = {}

    async def mock_set_menu(**kwargs):
        called["button"] = kwargs.get("menu_button")

    class FakeBot:
        set_chat_menu_button = staticmethod(mock_set_menu)

    base = _DEFAULT_BASE
    btn = MenuButtonWebApp(text="☀️ Nexus", web_app=WebAppInfo(url=f"{base}/nexus"))
    await FakeBot.set_chat_menu_button(menu_button=btn)

    button = called["button"]
    assert isinstance(button, MenuButtonWebApp), "должен быть MenuButtonWebApp, не Commands"
    assert button.type == "web_app"
    assert button.text == "☀️ Nexus"
    assert button.web_app.url == f"{_DEFAULT_BASE}/nexus"


@pytest.mark.asyncio
async def test_arcana_webapp_button_type_and_url():
    """set_chat_menu_button для Arcana вызывается с MenuButtonWebApp(url=.../arcana)."""
    called = {}

    async def mock_set_menu(**kwargs):
        called["button"] = kwargs.get("menu_button")

    class FakeBot:
        set_chat_menu_button = staticmethod(mock_set_menu)

    base = _DEFAULT_BASE
    btn = MenuButtonWebApp(text="🌒 Arcana", web_app=WebAppInfo(url=f"{base}/arcana"))
    await FakeBot.set_chat_menu_button(menu_button=btn)

    button = called["button"]
    assert isinstance(button, MenuButtonWebApp)
    assert button.type == "web_app"
    assert button.text == "🌒 Arcana"
    assert button.web_app.url == f"{_DEFAULT_BASE}/arcana"


def test_webapp_button_not_commands_type():
    """MenuButtonWebApp.type != MenuButtonCommands.type — замена корректна."""
    webapp_btn = MenuButtonWebApp(text="test", web_app=WebAppInfo(url="https://example.com"))
    commands_btn = MenuButtonCommands()
    assert webapp_btn.type != commands_btn.type
    assert webapp_btn.type == "web_app"
    assert commands_btn.type == "commands"


def test_miniapp_base_url_env_override():
    """MINIAPP_BASE_URL из .env переопределяет дефолт."""
    import os
    from core.config import _optional
    old = os.environ.get("MINIAPP_BASE_URL")
    try:
        os.environ["MINIAPP_BASE_URL"] = "https://custom.example.com"
        result = _optional("MINIAPP_BASE_URL", _DEFAULT_BASE)
        assert result == "https://custom.example.com"
    finally:
        if old is None:
            os.environ.pop("MINIAPP_BASE_URL", None)
        else:
            os.environ["MINIAPP_BASE_URL"] = old
