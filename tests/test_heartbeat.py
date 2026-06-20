"""Тесты core/heartbeat — фоновый heartbeat для docker healthcheck.

Покрываем:
- write_heartbeat пишет файл с целым unix-timestamp;
- timestamp растёт между записями;
- ошибка записи (плохой путь) не бросает — задача/бот не падает;
- start_heartbeat пишет файл сразу (до первого sleep) и крутит цикл;
- start_heartbeat идемпотентен (не плодит вторую задачу), держит strong-ref.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from core import heartbeat


@pytest.fixture(autouse=True)
def _reset_hb_task():
    """Сбросить модульный strong-ref между тестами, чтобы идемпотентность
    одного теста не утекала в другой."""
    heartbeat._hb_task = None
    yield
    t = heartbeat._hb_task
    if t is not None and not t.done():
        t.cancel()
    heartbeat._hb_task = None


# ── write_heartbeat (unit) ───────────────────────────────────────────────────

def test_write_heartbeat_writes_int_timestamp(tmp_path):
    p = tmp_path / "heartbeat"
    assert heartbeat.write_heartbeat(str(p)) is True
    val = p.read_text()
    assert val.isdigit()
    assert abs(int(val) - int(time.time())) < 5


def test_write_heartbeat_timestamp_grows(tmp_path, monkeypatch):
    p = tmp_path / "heartbeat"
    seq = iter([1000, 2000])
    monkeypatch.setattr(heartbeat.time, "time", lambda: next(seq))
    heartbeat.write_heartbeat(str(p))
    first = p.read_text()
    heartbeat.write_heartbeat(str(p))
    second = p.read_text()
    assert first == "1000"
    assert second == "2000"
    assert int(second) > int(first)


def test_write_heartbeat_error_does_not_raise():
    # Запись в несуществующую директорию → False, но НЕ исключение.
    ok = heartbeat.write_heartbeat("/no/such/dir/heartbeat_xyz")
    assert ok is False


# ── start_heartbeat (фоновая задача) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_heartbeat_writes_immediately_and_loops(tmp_path):
    p = tmp_path / "heartbeat"
    task = heartbeat.start_heartbeat(path=str(p), interval=0.01)
    # первый удар — синхронно, до первого sleep
    assert p.exists()
    first = int(p.read_text())
    assert not task.done()           # задача жива
    assert heartbeat._hb_task is task  # strong-ref держится
    # дать циклу несколько итераций
    await asyncio.sleep(0.05)
    assert p.exists()
    assert int(p.read_text()) >= first


@pytest.mark.asyncio
async def test_start_heartbeat_idempotent(tmp_path):
    p = tmp_path / "heartbeat"
    t1 = heartbeat.start_heartbeat(path=str(p), interval=1)
    t2 = heartbeat.start_heartbeat(path=str(p), interval=1)
    assert t1 is t2  # вторая задача не создалась


@pytest.mark.asyncio
async def test_start_heartbeat_loop_survives_write_error():
    """Сбой записи (битый путь) не роняет фоновую задачу — цикл крутится дальше."""
    # Каждая запись падает внутри write_heartbeat (директории нет) и глотается;
    # _heartbeat_loop продолжает тикать, задача жива.
    task = heartbeat.start_heartbeat(path="/no/such/dir/hb_loop", interval=0.01)
    await asyncio.sleep(0.05)
    assert not task.done()
