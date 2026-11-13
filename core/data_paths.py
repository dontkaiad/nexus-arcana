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


# Реестр SQLite-файлов, которые обязаны жить в data/ (volume).
# имя файла → (модуль, атрибут с путём, группа). Группа решает, какой бот
# мигрирует/логирует файл на старте: shared — оба, nexus / arcana — свой.
PERSISTENT_DBS = {
    "message_pages.db": ("core.message_pages", "DB_PATH", "shared"),
    "task_reminder_msg.db": ("core.task_reminder_msg", "DB_PATH", "shared"),
    "pending_kv.db": ("core.pending_kv", "_DB_PATH", "shared"),
    "pending_lists.db": ("core.list_manager", "_PENDING_DB", "shared"),
    "spell_whitelist.db": ("core.preprocess", "_WHITELIST_DB", "shared"),
    "session_cache.db": ("core.session_cache", "_DB_PATH", "arcana"),
    "pending_tasks.db": ("nexus.handlers.tasks", "_PENDING_DB", "nexus"),
    "pending_note_edit.db": ("nexus.pending_note_edit", "DB_PATH", "nexus"),
    "pending_clients.db": ("arcana.pending_clients", "DB_PATH", "arcana"),
    "pending_client_photo.db": ("arcana.pending_client_photo", "DB_PATH", "arcana"),
    "pending_tarot.db": ("arcana.pending_tarot", "DB_PATH", "arcana"),
    "pending_grimoire_search.db": ("arcana.pending_grimoire_search", "DB_PATH", "arcana"),
    "pending_writeoff.db": ("arcana.handlers.ritual_writeoff", "_DB", "arcana"),
    "pending_works.db": ("arcana.handlers.work_preview", "_PENDING_DB", "arcana"),
    "pending_barter.db": ("arcana.handlers.barter_prompt", "_DB", "arcana"),
}


def _names_for(groups) -> list:
    return [n for n, (_m, _a, g) in PERSISTENT_DBS.items() if g in groups]


def migrate_legacy_dbs(groups=("shared", "nexus", "arcana")) -> dict:
    """migrate_legacy_db для всех файлов групп. Возвращает {имя: новый путь} для перенесённых."""
    moved = {}
    for name in _names_for(groups):
        out = migrate_legacy_db(name)
        if out:
            moved[name] = out
    return moved


def log_persistent_dbs(groups=("shared", "nexus", "arcana")) -> None:
    """Стартовый лог: абсолютный путь КАЖДОЙ БД группы (берём реальный атрибут
    модуля, а не рассчитанный) и лежит ли она в data/."""
    import importlib

    d = data_dir()
    for name in _names_for(groups):
        mod_name, attr, _g = PERSISTENT_DBS[name]
        try:
            path = os.path.abspath(str(getattr(importlib.import_module(mod_name), attr)))
        except Exception as e:  # модуль группы может не импортироваться в чужом боте
            logger.warning("persistent db %s: cannot resolve (%s.%s): %s", name, mod_name, attr, e)
            continue
        logger.info(
            "persistent db %s: path=%s in_data_dir=%s exists=%s",
            name, path, os.path.dirname(path) == d, os.path.exists(path),
        )
