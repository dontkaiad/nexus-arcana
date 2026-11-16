"""Строка аудита Зари в топик 1182 уходит в фоне: хендлер не ждёт лог-группу (отправка туда нередко дольше 5 с),
задача держится по ссылке в наборе, таймаут больше обычного, ретрая нет, сбой не доходит до хендлера."""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core import bot_notify
from core.config import config
from zarya import handlers

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_tasks():
    handlers._LOG_TASKS.clear()
    yield
    handlers._LOG_TASKS.clear()


@pytest.mark.asyncio
async def test_notify_owner_returns_before_the_log_send_finishes():
    release = asyncio.Event()

    async def slow(*a, **k):
        await release.wait()

    send = AsyncMock(side_effect=slow)
    with patch("core.bot_notify.notify_booking_log", send):
        # хендлер не ждёт: вернулся, хотя отправка ещё висит (на мутации «await на горячем пути» это зависает)
        await asyncio.wait_for(handlers._notify_owner("⭐ Новая бронь"), timeout=2)
        await asyncio.sleep(0)
        assert len(handlers._LOG_TASKS) == 1 and not release.is_set()
        release.set()
        await asyncio.gather(*handlers._LOG_TASKS)
    send.assert_awaited_once_with("⭐ Новая бронь", timeout=handlers._BOOKING_LOG_TIMEOUT)


@pytest.mark.asyncio
async def test_the_task_is_held_by_reference_and_released_when_done():
    release = asyncio.Event()

    async def slow(*a, **k):
        await release.wait()

    with patch("core.bot_notify.notify_booking_log", AsyncMock(side_effect=slow)):
        await asyncio.wait_for(handlers._notify_owner("x"), timeout=2)
        (task,) = handlers._LOG_TASKS                     # без ссылки задачу может собрать сборщик мусора
        assert isinstance(task, asyncio.Task) and not task.done()
        release.set()
        await task
        await asyncio.sleep(0)
    assert handlers._LOG_TASKS == set()                  # и набор не растёт


@pytest.mark.asyncio
async def test_a_failing_background_send_never_reaches_the_handler_and_logs_only_the_class(caplog):
    boom = AsyncMock(side_effect=RuntimeError("секрет: 123:TOKEN в URL"))
    with patch("core.bot_notify.notify_booking_log", boom), caplog.at_level(logging.WARNING, logger="zarya.handlers"):
        await asyncio.wait_for(handlers._notify_owner("x"), timeout=2)   # не бросает
        await asyncio.gather(*handlers._LOG_TASKS)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged and "секрет" not in logged and "TOKEN" not in logged
    boom.assert_awaited_once()                           # без ретрая: дубль возможен, если Telegram принял запрос позже


@pytest.mark.asyncio
async def test_owner_dms_are_still_sent_in_the_handler_and_the_log_line_follows(monkeypatch):
    monkeypatch.setattr(config, "allowed_ids", [11, 22], raising=False)
    bot = AsyncMock()
    with patch("core.bot_notify.notify_booking_log", AsyncMock()) as send:
        await asyncio.wait_for(handlers._notify_owner("t", bot=bot), timeout=2)
        await asyncio.gather(*handlers._LOG_TASKS)
    assert [c.args[0] for c in bot.send_message.await_args_list] == [11, 22]
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_running_loop_is_swallowed_not_raised(caplog):
    with patch.object(handlers, "_spawn_booking_log", side_effect=RuntimeError("no running event loop")), \
            caplog.at_level(logging.WARNING, logger="zarya.handlers"):
        await handlers._notify_owner("x")
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


def test_the_hot_path_never_awaits_the_booking_log_directly():
    """В zarya/handlers.py `notify_booking_log` вызывается только из фоновой задачи."""
    tree = ast.parse((REPO / "zarya" / "handlers.py").read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name != "_send_booking_log":
            for node in ast.walk(fn):
                if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                    name = getattr(node.value.func, "id", getattr(node.value.func, "attr", ""))
                    assert name not in ("notify_booking_log", "_send_booking_log"), (fn.name, node.lineno)


# --- таймаут: по умолчанию 5 с, у фоновой отправки больше ---------------------------

class _FakeClient:
    seen: list = []

    def __init__(self, *, timeout):
        type(self).seen.append(timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}},
                              request=httpx.Request("POST", url))


@pytest.mark.asyncio
async def test_default_timeout_stays_five_seconds_and_the_background_one_is_larger(monkeypatch):
    monkeypatch.setattr(config, "log_bot_token", "123:T")
    monkeypatch.setattr(config, "log_chat_id", "-1001")
    monkeypatch.setattr(config, "log_thread_booking", "1182")
    _FakeClient.seen = []
    with patch.object(bot_notify.httpx, "AsyncClient", _FakeClient):
        assert await bot_notify.notify_booking_log("a") is True                      # прочие вызовы
        assert await bot_notify.notify_booking_log("b", timeout=handlers._BOOKING_LOG_TIMEOUT) is True
    assert _FakeClient.seen == [bot_notify._LOG_TIMEOUT, handlers._BOOKING_LOG_TIMEOUT]
    assert bot_notify._LOG_TIMEOUT == 5.0 and 15 <= handlers._BOOKING_LOG_TIMEOUT <= 20
