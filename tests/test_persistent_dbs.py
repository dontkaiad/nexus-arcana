"""tests/test_persistent_dbs.py — все SQLite бота живут в data/ (docker-volume).

В compose на volume смонтирован только /app/data. Любая SQLite в корне
репо (pending_*.db, session_cache.db, task_reminder_msg.db …) стиралась
каждым деплоем: терялись pending-диалоги и reply-контекст. Реестр
core.data_paths.PERSISTENT_DBS — единый список таких файлов.
"""
from __future__ import annotations

import importlib
import os
import re

import pytest

from core import data_paths

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_registered_db_is_built_from_data_db_path():
    """Каждый модуль реестра берёт путь из data_db_path(<имя>) — по исходнику, а не по
    живому атрибуту: часть тестов подменяет пути модулей на temp-файлы при импорте."""
    for name, (mod, attr, _group) in data_paths.PERSISTENT_DBS.items():
        src_path = os.path.join(REPO, *mod.split(".")) + ".py"
        src = open(src_path, encoding="utf-8").read()
        assert re.search(rf'{attr}\s*=\s*(?:Path\()?_?data_db_path\("{re.escape(name)}"\)', src), \
            f"{mod}.{attr} не строится через data_db_path({name!r})"


def test_data_db_path_is_inside_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "vol"))
    p = data_paths.data_db_path("pending_kv.db")
    assert p == str(tmp_path / "vol" / "pending_kv.db") and (tmp_path / "vol").is_dir()


def test_no_sqlite_path_points_to_repo_root():
    """Страж: новые `.. / x.db` пути в корень (вне data/) ронят тест."""
    root_rx = re.compile(r'"\.\."[^\n]*\.db"|"\.\./[\w.]+\.db"|parent\.parent\s*/\s*"[\w.]+\.db"')
    offenders = []
    for base in ("core", "nexus", "arcana", "miniapp/backend"):
        for dp, _dn, files in os.walk(os.path.join(REPO, base)):
            if "__pycache__" in dp or "node_modules" in dp:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                fp = os.path.join(dp, f)
                for i, line in enumerate(open(fp, encoding="utf-8"), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if root_rx.search(line) and '"data"' not in line:  # …/"data"/x.db — уже в volume
                        offenders.append(f"{os.path.relpath(fp, REPO)}:{i}: {line.strip()}")
    assert not offenders, "SQLite в корне вне volume:\n" + "\n".join(offenders)


@pytest.fixture
def legacy_env(tmp_path, monkeypatch):
    legacy = tmp_path / "root"
    legacy.mkdir()
    monkeypatch.setattr(data_paths, "_REPO_ROOT", str(legacy))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return legacy, tmp_path / "data"


def test_migrate_all_noop_when_nothing_in_root(legacy_env):
    _legacy, data = legacy_env
    assert data_paths.migrate_legacy_dbs() == {}
    assert not any(data.glob("*.db"))


def test_migrate_all_moves_present_files_only(legacy_env):
    legacy, data = legacy_env
    (legacy / "pending_lists.db").write_bytes(b"L")
    (legacy / "pending_tarot.db").write_bytes(b"T")
    moved = data_paths.migrate_legacy_dbs()
    assert set(moved) == {"pending_lists.db", "pending_tarot.db"}
    assert (data / "pending_lists.db").read_bytes() == b"L"
    assert not (legacy / "pending_tarot.db").exists()
    assert not (data / "pending_kv.db").exists()  # чего не было — не создаём


def test_migrate_all_never_overwrites_existing_data_file(legacy_env):
    legacy, data = legacy_env
    data.mkdir()
    (data / "pending_kv.db").write_bytes(b"fresh")
    (legacy / "pending_kv.db").write_bytes(b"old")
    (legacy / "pending_barter.db").write_bytes(b"B")
    moved = data_paths.migrate_legacy_dbs()
    assert "pending_kv.db" not in moved and "pending_barter.db" in moved
    assert (data / "pending_kv.db").read_bytes() == b"fresh"
    assert (legacy / "pending_kv.db").read_bytes() == b"old"


def test_migrate_groups_are_respected(legacy_env):
    legacy, data = legacy_env
    (legacy / "pending_tarot.db").write_bytes(b"arcana-only")
    (legacy / "pending_note_edit.db").write_bytes(b"nexus-only")
    assert set(data_paths.migrate_legacy_dbs(("shared", "nexus"))) == {"pending_note_edit.db"}
    assert (legacy / "pending_tarot.db").exists()  # чужая группа не тронута


def test_log_reports_every_db_of_group(caplog, monkeypatch):
    # живые атрибуты могут быть подменены другими тестами → подставляем честные пути
    def fake_import(mod_name):
        ns = type("M", (), {})()
        for n, (m, attr, _g) in data_paths.PERSISTENT_DBS.items():
            if m == mod_name:
                setattr(ns, attr, data_paths.data_db_path(n))
        return ns

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with caplog.at_level("INFO", logger="core.data_paths"):
        data_paths.log_persistent_dbs(("shared", "arcana"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    names = [n for n, (_m, _a, g) in data_paths.PERSISTENT_DBS.items() if g in ("shared", "arcana")]
    for n in names:
        assert f"persistent db {n}: path=" in text
    assert text.count("in_data_dir=True") == len(names)
    assert "pending_tasks.db" not in text  # nexus-only в логе арканы не нужен


@pytest.mark.parametrize("rel,groups", [
    ("nexus/nexus_bot.py", '("shared", "nexus")'),
    ("arcana/bot.py", '("shared", "arcana")'),
])
def test_both_bots_migrate_and_log_on_start(rel, groups):
    src = open(os.path.join(REPO, rel), encoding="utf-8").read()
    assert f"migrate_legacy_dbs({groups})" in src
    assert f"log_persistent_dbs({groups})" in src
