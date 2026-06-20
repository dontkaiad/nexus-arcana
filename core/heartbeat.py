"""core/heartbeat.py — фоновый heartbeat для docker healthcheck.

Фоновая asyncio-задача раз в HEARTBEAT_INTERVAL сек перезаписывает
HEARTBEAT_FILE текущим unix-временем. docker healthcheck читает файл и
считает контейнер unhealthy, если время протухло (now - mtime ≥ порога) —
т.е. процесс завис / event loop встал. Проверяется СВЕЖЕСТЬ, а не наличие
файла: залипший файл мёртвого процесса не должен выглядеть здоровым.

Запись обёрнута в try/except — сбой записи (нет места, права) НЕ роняет бота.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("heartbeat")

HEARTBEAT_FILE = "/tmp/heartbeat"
HEARTBEAT_INTERVAL = 30  # сек

# Strong-ref на фоновую задачу — иначе asyncio может собрать её GC до
# завершения (create_task возвращает «слабую» по сути ссылку).
_hb_task: Optional[asyncio.Task] = None


def write_heartbeat(path: str = HEARTBEAT_FILE) -> bool:
    """Перезаписать файл текущим unix-временем. Никогда не бросает.

    Возвращает True если записали успешно, False при ошибке (залогирована).
    """
    try:
        with open(path, "w") as f:
            f.write(str(int(time.time())))
        return True
    except Exception as e:
        logger.warning("heartbeat write failed: %s", e)
        return False


async def _heartbeat_loop(path: str, interval: int) -> None:
    while True:
        write_heartbeat(path)
        await asyncio.sleep(interval)


def start_heartbeat(
    path: str = HEARTBEAT_FILE, interval: int = HEARTBEAT_INTERVAL,
) -> asyncio.Task:
    """Запустить фоновый heartbeat: первый удар сразу + раз в interval сек.

    Держит strong-ref в модульном _hb_task. Идемпотентно: повторный вызов
    при живой задаче не плодит вторую. Вызывать внутри running loop (из
    startup-хука aiogram).
    """
    global _hb_task
    if _hb_task is not None and not _hb_task.done():
        return _hb_task
    write_heartbeat(path)  # первый удар синхронно, до первого sleep
    _hb_task = asyncio.create_task(_heartbeat_loop(path, interval))
    return _hb_task
