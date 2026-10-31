"""GIT_SHA из ENV попадает в лог старта бота; Dockerfile'ы принимают build-arg."""
from __future__ import annotations

import logging
from pathlib import Path

from core import bot_notify

REPO = Path(__file__).resolve().parent.parent


def test_git_sha_from_env_and_log(monkeypatch, caplog):
    monkeypatch.setenv("GIT_SHA", "abc1234")
    assert bot_notify.git_sha() == "abc1234"
    with caplog.at_level(logging.INFO, logger=bot_notify.logger.name):
        bot_notify.log_version("nexus")
    assert "bot=nexus GIT_SHA=abc1234" in caplog.text


def test_git_sha_unknown_when_unset(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert bot_notify.git_sha() == "unknown"


def test_dockerfiles_bake_git_sha_into_env():
    for f in ("Dockerfile", "zarya/Dockerfile"):
        t = (REPO / f).read_text()
        assert "ARG GIT_SHA=" in t and "ENV GIT_SHA=${GIT_SHA}" in t, f
    z = (REPO / "zarya/Dockerfile").read_text()
    assert z.index("ENV GIT_SHA") > z.index("COPY arcana/repos/__init__.py")   # после всех COPY — кеш слоёв цел
    assert "RUN" not in z[z.index("ARG GIT_SHA="):].split("CMD")[0].replace("RUN pip", "")
