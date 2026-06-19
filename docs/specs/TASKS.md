# TASKS — data-model contract (Nexus ✅ Задачи)

Code conforms to: e938907. This spec describes the tasks data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants — the things that should not drift with
> ordinary commits. Enumerations point at the code constant that owns them
> rather than restating it. Every claim is verifiable from the files in
> "Verify against code".

## Purpose

Nexus tasks ("✅ Задачи") — the user's actionable to-do items: title, status,
priority, category, optional deadline/reminder, and optional repetition.
Storage is PostgreSQL (`tasks` table + five normalized lookup tables).
Reminders are scheduled jobs (APScheduler), not a column; streaks are tracked
in a separate SQLite store. Tasks belong to a user via `user_notion_id`.

## Schema

One owning table `tasks` plus five seeded lookup tables. Migration:
`alembic/versions/h8c9d0e1f2a3_nexus_tasks_pg.py` (revision `h8c9d0e1f2a3`,
down_revision `g7b8c9d0e1f2`). SQLAlchemy Core mirror:
`nexus/repos/tasks_tables.py` (column-for-column).

### `tasks`

| Column | Type | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `notion_id` | Text | UNIQUE (nullable) — legacy Notion-migration artifact; not written by the current create path; slated for removal (see #149) |
| `title` | Text | NOT NULL |
| `status_id` | SmallInteger | NOT NULL, FK → `task_status.id` |
| `repeat_id` | SmallInteger | FK → `task_repeat.id` (nullable) |
| `day_of_week_id` | SmallInteger | FK → `task_day_of_week.id` (nullable) |
| `priority_id` | SmallInteger | FK → `task_priority.id` (nullable) |
| `category_id` | SmallInteger | FK → `task_category.id` (nullable) |
| `deadline` | TIMESTAMP(tz) | nullable |
| `reminder` | TIMESTAMP(tz) | nullable |
| `completed_at` | TIMESTAMP(tz) | nullable |
| `repeat_time` | Text | nullable — free-form repeat spec (see Recurring) |
| `parent_task_id` | BigInteger | self-FK → `tasks.id` `ON DELETE SET NULL` |
| `user_notion_id` | Text | NOT NULL, default `''` |
| `created_at` | TIMESTAMP(tz) | NOT NULL, default `now()` |
| `updated_at` | TIMESTAMP(tz) | NOT NULL, default `now()` |

Indexes: `idx_tasks_user_notion_id` (user_notion_id),
`idx_tasks_status_id` (status_id).

### Lookup tables (`id SMALLINT PK`, `code TEXT UNIQUE`)

`task_status`, `task_repeat`, `task_day_of_week`, `task_priority`,
`task_category`. The code set is owned and seeded by the migration
`upgrade()` (the source of truth) and cached once per process
(`pg_tasks_repo._load_lookups_sync`) — do not restate it in full here.
Examples below are non-exhaustive; see the migration for the full seeded set:
- `task_status`: `Not started`, `In progress`, `Done`, `Archived` (examples, non-exhaustive — see migration for the full seeded set).
- `task_repeat`: `Нет`, `Ежедневно`, `Еженедельно`, `Ежемесячно` (examples, non-exhaustive — see migration for the full seeded set).
- `task_priority`: `🟡 Важно`, `🔴 Срочно` (examples, non-exhaustive — see migration for the full seeded set).
- `task_category`: `👥 Люди`, `💳 Прочее`, `🐾 Коты` (examples, non-exhaustive — see migration for the full seeded set).

### Domain object

`nexus/repos/pg_tasks_repo.py:Task` (`@dataclass`) exposes lookup **codes**,
not ids: `status`, `priority`, `category`, `repeat`, `day_of_week` are mapped
from `*_id` via the cache (`_to_task`). Datetime columns are surfaced as ISO
strings (`deadline`, `reminder`, `completed_at`, `created_at`,
`last_edited`←`updated_at`); empty string when null.

## Operations & contract

All writes go through `PgTasksRepo` (async facade over `asyncio.to_thread`
sync helpers). Lookup resolution is fuzzy: `_match` tries exact code, then
case-insensitive substring either direction, then a default
(`pg_tasks_repo._match`). Callers pass Notion-shaped `props` dicts; the repo
extracts/normalizes them — a leftover of the Notion-era interface.

- **create** — `create(_db_id, props)`. Extracts `Задача`/`Статус`/
  `Приоритет`/`Категория`/`Дедлайн`/`Напоминание` and the owning user from
  the `🪪 Пользователи` relation. Guarantees: `status` defaults to
  `Not started`, `priority` to `🟡 Важно`, `category` to `💳 Прочее` when
  unresolved; dates parsed via `_parse_iso` (naive → UTC). Returns the new
  id as `str`, or `None`. `repeat_*`/`parent_task_id` are NOT set on create.
- **subtasks** — the "📋 Подзадачи" button (`core/subtasks_handler.py`,
  one factory router shared by both bots) writes child items into 🗒️ Списки
  with a relation back to the parent task/work. It does NOT create `tasks`
  rows and does NOT populate `parent_task_id`. Contract consequence:
  `parent_task_id` is a self-FK present in the schema but not written by the
  task-creation/subtask flow.
- **status change** — `set_status(id, code)` (fuzzy-matched),
  `set_in_progress`, `set_archived`. All stamp `updated_at = now()`. A code
  that matches nothing → no-op `False` (status never silently corrupted).
- **field edit** — `set_props(id, props)` maps Notion fields
  (`Задача`/`Статус`/`Приоритет`/`Категория`/`Повтор`/`День недели`/
  `Время повтора`/`Дедлайн`/`Напоминание`/`Время завершения`) onto columns;
  no-op if nothing but `updated_at` resolves.
- **repeat fields** — `set_repeat_fields(id, repeat, day_of_week,
  repeat_time)` sets `repeat_id`/`day_of_week_id`/`repeat_time`.
- **complete** — non-recurring: status → `Done`, reminder/deadline jobs
  removed. Recurring (see Invariants): status is held at `In progress`,
  `completed_at` ("Время завершения") stamped = now as the "done today"
  marker, and `deadline`/`reminder` advanced to the next cycle
  (`_handle_recurring_task_reset`).
- **reschedule** — reminders are APScheduler jobs keyed `reminder_{id}` /
  `deadline_{id}`, rebuilt (not stored) from the `reminder`/`deadline`
  columns. Timezone change re-shifts all future reminders
  (`_reschedule_all_for_tz`); "⏳ Отложить" reschedules a single task.
- **recurring** — `repeat_time` (free text, parsed by
  `tasks._parse_repeat_time`) drives the next-run time and interval; the
  `repeat` code drives the period. Reminders advance per cycle rather than
  the task reaching a terminal state.

## Invariants

- **Status set** is exactly `task_status.code` (FK-enforced):
  `Not started` → `In progress` → `Done`, plus `Archived`. No free-text
  status.
- **Active queries exclude `Done` and `Archived`** (`_list_active_sync`,
  and the reminder-restore queries). `active(include_in_progress=False)`
  additionally drops `In progress`. Active list is ordered by `priority_id`
  ascending, nulls last.
- **A recurring task never reaches `Done` via its reminder.** Reminder-done
  on a recurring task → `In progress` (`_handle_recurring_reminder_done`);
  only the deadline path (or a recurring task with no deadline) advances the
  cycle. Between cycles a recurring task stays `In progress` with
  `completed_at` marking the last completion.
- **Recurring-without-reminder is revived on startup.** `restore_reminders_
  on_startup` pass 3: a non-terminal task with `repeat_time` set but
  `reminder IS NULL` gets its first future run computed from `repeat_time`,
  persisted to `reminder`, and scheduled
  (`active_recurring_without_reminder`). Pass 1 reschedules future
  reminders; pass 2 advances/handles past-due ones.
- **Streaks are not in `tasks`.** They live in two SQLite tables in
  `data/nexus_streaks.db` (per-task + global daily); verified no other streak
  writer in the codebase as of e938907 (only `core/task_streaks.py` writes
  `task_streaks`, and `nexus/handlers/streaks.py` writes `streaks` plus a
  `streak_calls` log; arcana has none, the Mini App only delegates to these):
  - per-task streak — `core/task_streaks.py` (table `task_streaks`,
    PK `(user_id, task_id)`); extended only for repeating tasks. Rule:
    same-day completion is a no-op; completion exactly one period after the
    last extends `current` (`best = max`); otherwise `current` resets to 1.
    Daily streaks are lazily reset when a day is missed
    (`reset_broken_streaks`); weekly/monthly are not auto-reset. Written
    only from the Mini App completion path.
  - global daily streak — `nexus/handlers/streaks.py`, incremented on ANY
    `Done` task from the bot path (`_update_streak_line`,
    `source="bot_task_done"`).
- **`reminder`/`deadline` are projections.** APScheduler jobs are derived
  from these columns and rebuilt on startup; the columns are the source of
  truth, the jobs are disposable.

## Lifecycle / status model

```
create → Not started ──set_status──▶ In progress ──▶ Done
                                   └────────────────▶ Archived (set_archived)
recurring complete: stays In progress, completed_at=now, deadline/reminder advanced
```

`completed_at` is set for both terminal `Done` (one-shot) and per-cycle
recurring completion; for recurring it is the "done this cycle" marker the
Mini App uses to hide the task until the next run.

## Callers

- Bot — `nexus/handlers/tasks.py`: parse/create, completion callbacks,
  recurring reset, reminder restore, timezone reschedule;
  `nexus/handlers/streaks.py` (global daily streak).
- Shared — `core/subtasks_handler.py` ("📋 Подзадачи" → 🗒️ Списки relation).
- Repos — `nexus/repos/tasks_repo.py` (seam) → `nexus/repos/pg_tasks_repo.py`
  → `nexus/repos/tasks_tables.py`.
- Mini App — `miniapp/backend/routes/tasks.py` (`GET /api/tasks`,
  serialize), `miniapp/backend/routes/writes.py` (status write +
  per-task/global streak update), `miniapp/backend/routes/streaks.py`
  (`reset_broken_streaks` + `get_user_task_streaks`).
- Backfill — `scripts/backfill_tasks.py` (Notion → PG, uses `notion_id`).

## Model routing (from code)

`nexus/handlers/tasks.py` uses Haiku exclusively
(`claude-haiku-4-5-20251001`): parsing date/priority/category/repeat from
free text and short ADHD advice lines. No Sonnet, no Opus in the tasks path.
Reads/writes/status/streak logic are pure SQL/SQLite — no LLM.

## Verify against code

- `alembic/versions/h8c9d0e1f2a3_nexus_tasks_pg.py` — tables + seeded codes
- `nexus/repos/tasks_tables.py` — SQLAlchemy Core definitions
- `nexus/repos/pg_tasks_repo.py` — `Task` dataclass, lookup cache, `_match`,
  create/status/props/repeat, reminder-restore queries
- `nexus/repos/tasks_repo.py` — repository seam
- `nexus/handlers/tasks.py` — create/complete/recurring reset
  (`_handle_recurring_task_reset`, `_handle_recurring_reminder_done`),
  `restore_reminders_on_startup`, `_parse_repeat_time`, `_reschedule_all_for_tz`,
  `_update_streak_line`, Haiku `ask_claude` calls
- `core/task_streaks.py` — per-task streak store + rules
- `nexus/handlers/streaks.py` — global daily streak
- `core/subtasks_handler.py` — "📋 Подзадачи" factory router → 🗒️ Списки
- `miniapp/backend/routes/tasks.py` — `GET /api/tasks`
- `miniapp/backend/routes/writes.py` — completion write + streak updates
- `miniapp/backend/routes/streaks.py` — per-task streak read/reset
- `scripts/backfill_tasks.py` — Notion → PG backfill
