"""core/data_paths.py — пути к SQLite-файлам, которые ДОЛЖНЫ переживать деплой.

В docker-compose на volume смонтирован только `/app/data` (#191). Всё, что
лежит рядом (корень репо), стирается при каждом пересоздании контейнера.
`message_pages.db` (reply на карточку → задача) и `pending_tasks.db`
(`last_task`, pending-превью) жили в корне и терялись при каждом деплое —
reply на карточку после рестарта уходил в создание новой задачи.

DATA_DIR можно переопределить env-переменной (по умолчанию `<repo>/data`).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("core.data_paths")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    return os.path.abspath(os.environ.get("DATA_DIR") or os.path.join(_REPO_ROOT, "data"))


def data_db_path(name: str) -> str:
    """Абсолютный путь к `<DATA_DIR>/<name>` (папка создаётся при необходимости)."""
    d = data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:  # не блокируем импорт: sqlite сам скажет, если писать нельзя
        logger.warning("data_db_path: cannot create %s: %s", d, e)
    return os.path.join(d, name)


def migrate_legacy_db(name: str, legacy_dir: Optional[str] = None) -> Optional[str]:
    """Перенести `<legacy_dir>/<name>` (по умолчанию корень репо) в DATA_DIR.

    - Файла в корне нет → no-op (None).
    - Целевой файл в data/ уже есть → НИКОГДА не затираем, старый не трогаем (None).
    - Иначе перемещаем (os.rename, при разных ФС — копирование) вместе с -wal/-shm
      и возвращаем новый путь.
    """
    src = os.path.join(legacy_dir or _REPO_ROOT, name)
    dst = data_db_path(name)
    if os.path.abspath(src) == os.path.abspath(dst) or not os.path.isfile(src):
        return None
    if os.path.exists(dst):
        logger.warning("migrate_legacy_db: %s already exists, legacy %s left untouched", dst, src)
        return None
    import shutil
    try:
        shutil.move(src, dst)
        for suffix in ("-wal", "-shm", "-journal"):
            if os.path.isfile(src + suffix) and not os.path.exists(dst + suffix):
                shutil.move(src + suffix, dst + suffix)
    except OSError as e:
        logger.error("migrate_legacy_db: %s -> %s failed: %s", src, dst, e)
        return None
    logger.info("migrate_legacy_db: moved %s -> %s", src, dst)
    return dst


def log_persistent_dbs() -> None:
    """Стартовый лог: абсолютные пути обеих БД и лежат ли они в data/."""
    from core import message_pages
    from nexus.handlers import tasks

    d = data_dir()
    for label, path in (("message_pages", message_pages.DB_PATH), ("pending_tasks", tasks._PENDING_DB)):
        p = os.path.abspath(path)
        logger.info(
            "persistent db %s: path=%s in_data_dir=%s exists=%s",
            label, p, os.path.dirname(p) == d, os.path.exists(p),
        )
