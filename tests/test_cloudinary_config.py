"""tests/test_cloudinary_config.py — _config() читает env в обоих форматах."""
from __future__ import annotations

import pytest

from core import cloudinary_client


@pytest.fixture(autouse=True)
def _reset_log_once():
    cloudinary_client._log_once_state["logged"] = False
    yield
    cloudinary_client._log_once_state["logged"] = False


def _clear_env(monkeypatch):
    for k in (
        "CLOUDINARY_URL",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)


def test_config_url_only(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://key123:secret456@mycloud")
    cfg = cloudinary_client._config()
    assert cfg == ("mycloud", "key123", "secret456")


def test_config_separate_keys_only(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "mycloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key123")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret456")
    cfg = cloudinary_client._config()
    assert cfg == ("mycloud", "key123", "secret456")


def test_config_url_takes_priority(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://urlkey:urlsecret@urlcloud")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "altcloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "altkey")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "altsecret")
    cfg = cloudinary_client._config()
    assert cfg == ("urlcloud", "urlkey", "urlsecret")


def test_config_nothing_set_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    assert cloudinary_client._config() is None


def test_config_partial_separate_keys_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "mycloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key123")
    # secret отсутствует — конфиг неполный
    assert cloudinary_client._config() is None


def test_config_malformed_url_falls_back_to_separate(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLOUDINARY_URL", "not-a-cloudinary-url")
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "mycloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key123")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret456")
    cfg = cloudinary_client._config()
    assert cfg == ("mycloud", "key123", "secret456")
