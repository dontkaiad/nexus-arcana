"""scripts/deploy.sh — блок бэкапа перед `alembic upgrade head`.

Блок вырезается из скрипта и гоняется в bash с подставными `docker` и `df`
во временном HOME (ни БД, ни сети). Проверяем: чистятся только
predeploy_nexus_arcana_*, чужие файлы общей папки на месте, права, остановка
при сбое до alembic. Privacy: синтетические имена файлов.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = (REPO / "scripts" / "deploy.sh").read_text()
START = "# Pre-migration backup"
END = "docker compose run --rm nexus alembic upgrade head"
BLOCK = SCRIPT[SCRIPT.index(START):SCRIPT.index(END)]

FOREIGN = [
    "nexus_arcana_2026-11-12_0300.dump", "nexus_arcana_2026-11-13_0300.dump",   # cron
    "nexus_arcana_pre_sort_20261111_140054.dump", "nexus_arcana_pre_blocks_20261111_133125.dump",
    "auth_2026-11-13_0310.dump", "santa_2026-11-13_0305.dump",
    "backup.log", "cats_db_pre_x_20261110_224545.dump", "Caddyfile.pre-x.20261113",
]


def _shims(tmp: Path, dump_fails: bool = False) -> Path:
    b = tmp / "bin"
    b.mkdir()
    (b / "df").write_text("#!/bin/sh\necho Avail\necho 99999999999999\n")
    (b / "docker").write_text(f"""#!/bin/bash
case "$*" in
  *psql*) echo 1000 ;;
  *pg_dump*) {'exit 1' if dump_fails else 'echo PGDMP-fake'} ;;
  *pg_restore*) cat >/dev/null; echo '1; 1262 1 DATABASE - x'; echo '2; 1259 2 TABLE public t' ;;
esac
""")
    for f in ("df", "docker"):
        (b / f).chmod(0o755)
    return b


def _run(tmp: Path, bin_dir: Path):
    script = tmp / "run.sh"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + BLOCK + "echo REACHED-ALEMBIC\n")
    env = {**os.environ, "HOME": str(tmp / "home"), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)


def _prepare(tmp: Path, n_predeploy: int):
    home = tmp / "home"
    d = home / "backups"
    d.mkdir(parents=True)
    d.chmod(0o775)
    for name in FOREIGN:
        (d / name).write_text("keep:" + name)
    for i in range(1, n_predeploy + 1):
        f = d / f"predeploy_nexus_arcana_2026-10-{i:02d}_000000.dump"
        f.write_text("old")
        os.utime(f, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
    return d


def test_rotation_touches_only_predeploy_files(tmp_path):
    d = _prepare(tmp_path, 9)
    r = _run(tmp_path, _shims(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "REACHED-ALEMBIC" in r.stdout and "backup ok" in r.stdout
    for name in FOREIGN:                                   # чужое — на месте и не изменено
        assert (d / name).read_text() == "keep:" + name
    mine = sorted(p.name for p in d.glob("predeploy_nexus_arcana_*.dump"))
    assert len(mine) == 7                                  # 9 старых + 1 новый → 7
    assert "predeploy_nexus_arcana_2026-10-01_000000.dump" not in mine   # самые старые удалены
    assert "predeploy_nexus_arcana_2026-10-09_000000.dump" in mine
    assert not list(d.glob(".predeploy_*"))                # временных файлов нет


def test_new_dump_is_600_and_existing_dir_mode_is_kept(tmp_path):
    d = _prepare(tmp_path, 0)
    r = _run(tmp_path, _shims(tmp_path))
    assert r.returncode == 0, r.stderr
    new = [p for p in d.glob("predeploy_nexus_arcana_*.dump")]
    assert len(new) == 1 and stat.S_IMODE(new[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(d.stat().st_mode) == 0o775         # существующая папка не тронута


def test_directory_created_by_the_script_is_700(tmp_path):
    (tmp_path / "home").mkdir()
    r = _run(tmp_path, _shims(tmp_path))
    assert r.returncode == 0, r.stderr
    assert stat.S_IMODE((tmp_path / "home" / "backups").stat().st_mode) == 0o700


def test_failed_dump_stops_before_alembic_and_changes_nothing(tmp_path):
    d = _prepare(tmp_path, 9)
    before = sorted(p.name for p in d.iterdir())
    r = _run(tmp_path, _shims(tmp_path, dump_fails=True))
    assert r.returncode != 0 and "REACHED-ALEMBIC" not in r.stdout
    assert sorted(p.name for p in d.iterdir()) == before   # ни ротации, ни временных файлов


def test_block_is_right_before_alembic_and_script_syntax_ok():
    assert SCRIPT.count(END) == 1 and SCRIPT.index(START) < SCRIPT.index(END)
    assert subprocess.run(["bash", "-n", str(REPO / "scripts" / "deploy.sh")]).returncode == 0
