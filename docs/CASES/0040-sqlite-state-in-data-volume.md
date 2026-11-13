# Case 0040 — SQLite state lives in `data/` on a Docker volume

- **Status:** Accepted
- **Date:** 2026-11-13
- **Relates to:** Case 0014 (deploy), issue #191 (streaks lost on deploy)
- **Code:** `core/data_paths.py` (`PERSISTENT_DBS`, `data_db_path`, `migrate_legacy_dbs`, `log_persistent_dbs`), start of both bots,
  `tests/test_persistent_dbs.py`, `tests/test_task_card_reply_persist.py`

## Problem

The bot containers are recreated on every deploy (`docker compose up -d --build` after each green run on `main`). Only `/app/data` is a volume
(`nexus_sqlite_data`, `arcana_sqlite_data`). Part of the SQLite files was created in the repo root, inside the container filesystem, and was wiped
on every deploy: the card → task mapping (`message_pages.db`) and the pending task state (`pending_tasks.db`) first of all, so a reply to a card
sent before a deploy no longer found its task and became a new one.

## Decision

- All 15 SQLite files are listed in one registry, `PERSISTENT_DBS`, and every module builds its path with `data_db_path(name)` → `<DATA_DIR or ./data>/<name>`.
- **Migration on start:** `migrate_legacy_dbs(groups)` moves a file from the repo root into `data/` only if it is absent in `data/`. It never overwrites
  and never deletes a file that already lives in the volume. No file in the root → no-op.
- **Start log:** `log_persistent_dbs` prints one line per file (`persistent db <name>: path=… in_data_dir=… exists=…`); on the server `in_data_dir=True`
  for every file is the check that the volume is used.
- **Guard test:** `test_no_sqlite_path_points_to_repo_root` fails if any registered path points outside `data/`; a second test checks that every
  registered database is built from `data_db_path`. A new `*.db` in the root cannot appear unnoticed.

## Alternatives

- Mount the repo root as a volume: mixes code and state, breaks `git pull`.
- Move the state to Postgres: right long-term for pending state, but a rewrite of 15 modules; out of scope for a fix.

## Consequences

- State written before the move (card mappings of the old container) is not recoverable; only cards created after the deploy are mapped.
- Adding a SQLite file means adding it to `PERSISTENT_DBS`; the guard test enforces it.
