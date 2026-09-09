# TASKS — data-model contract (Nexus ✅ Задачи)

Code conforms to: b9d3367 (+ this change: #149 — notion_id column dropped). (+ #144: user_notion_id → user_id.) This spec describes the tasks data model as of
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
in a separate SQLite store. Tasks belong to a user via `user_id`.

## Schema

One owning table `tasks` plus five seeded lookup tables. Migration:
`alembic/versions/h8c9d0e1f2a3_nexus_tasks_pg.py` (revision `h8c9d0e1f2a3`,
down_revision `g7b8c9d0e1f2`). SQLAlchemy Core mirror:
`nexus/repos/tasks_tables.py` (column-for-column).

### `tasks`

| Column | Type | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
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
| `note` | Text | nullable — raw money/other detail that is neither deadline nor priority (see Deferred expense) |
| `parent_task_id` | BigInteger | self-FK → `tasks.id` `ON DELETE SET NULL` |
| `user_id` | Text | NOT NULL, default `''` |
| `created_at` | TIMESTAMP(tz) | NOT NULL, default `now()` |
| `updated_at` | TIMESTAMP(tz) | NOT NULL, default `now()` |

Indexes: `idx_tasks_user_id` (user_id),
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
  `Время повтора`/`Дедлайн`/`Напоминание`/`Время завершения`/`Заметка`) onto
  columns; no-op if nothing but `updated_at` resolves.
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

## Deferred expense (`note`)

A task whose text mentions money that will be spent *at the deadline*, not now
("прийти к нотариусу в среду, оплата 1250"), is classified as a single `task`
item with the raw mention in `note` — the classifier does **not** emit a
separate `expense` (see `core/classifier.build_system`). On completion of a
non-recurring task, `tasks._expense_from_note_on_done` →
`finance.expense_from_task_note` parses an amount from `note`
(`_parse_user_amount`) and, if found, writes a real `💸 Расход` transaction
dated today (reusing `_write_one_time_expense`) with a Haiku-picked category
(`_ONE_TIME_PARSE_SYSTEM`, default `💳 Прочее`) and posts "📤 Записал расход".
No amount in `note` → nothing written, completion is never blocked.

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
  `completed_at` marking the last completion. The reminder-done click still
  counts toward the **global daily streak** (`source="bot_recurring_reminder_done"`)
  even though the status stays `In progress` — «I did it today» is a streak
  day regardless of which ping was answered.
- **Recurring-without-reminder is revived on startup.** `restore_reminders_
  on_startup` pass 3: a non-terminal task with `repeat_time` set but
  `reminder IS NULL` gets its first future run computed from `repeat_time`,
  persisted to `reminder`, and scheduled
  (`active_recurring_without_reminder`). Pass 1 reschedules future
  reminders; pass 2 advances/handles past-due ones.
- **Deadline pings are re-armed on startup too** (pass 4,
  `active_with_future_deadline_no_reminder`, #212). A deadline job is only
  created when the task has no reminder (#69) and was previously never
  rebuilt after a restart — so a deadline-only ping was lost silently on
  every deploy. Pass 4 reschedules `_schedule_deadline_check` (with the same
  `recipients` fan-out as reminders). A deadline already in the past is not
  re-sent as a late ping — only future ones are re-armed.
- **Reminder jobs are re-armed from PG every 90 s, not only on startup**
  (`reminder_resync` `IntervalTrigger`, `restore_reminders_on_startup(periodic=True)`,
  #210). APScheduler keeps jobs in memory and loses them on every restart
  (frequent `auto-pull` + `watchfiles` on `nexus/`/`core/`/`miniapp/`); the
  interval sweep + the 120 s grace in `_schedule_reminder` mean a missed
  reminder arrives within ~90 s instead of on the next deploy. In `periodic` mode pass 1
  (re-arm future, `replace_existing`) and pass 3 (revive `reminder IS NULL`)
  run as normal, and pass 2 handles missed **one-off** reminders; missed
  **recurring** reminders are left for the next real startup — a periodic
  pass 2 would fire «⏰ Пропущено … переношу» on top of the live «🔔
  Напоминание» before the user answers.
- **`restore_reminders_on_startup` iterates distinct `user_id`s, not raw
  `allowed_ids`, and a reminder fans out to every TG chat of its owner**
  (#211). Kai's two Telegram accounts share one `user_id` (#202); the old
  per-`tg_id` loop scheduled each task once per account under the same job id
  `reminder_<task>`, so the second `add_job(replace_existing=True)` clobbered
  the first and the reminder reached only the last account in `allowed_ids`.
  Now there is one job per task; `_schedule_reminder(recipients=[...])`
  delivers the ping to all of the owner's chats at fire time (a per-target
  `send_message`, failures logged and skipped). The live create path still
  schedules to the single creating chat; the 90 s sweep upgrades it to the
  fan-out job on its next pass.
- **A one-off reminder is nulled once it has fired** (`clear_reminder`, #206).
  Both when it fires live (`_schedule_reminder.send_reminder`) and when
  pass 2 delivers it late as «⏰ Пропущено», `reminder` is set to `NULL`
  afterwards — otherwise `active_with_past_reminder` (filters only
  `Done`/`Archived`) would return the task again on the next restart and
  re-send the missed ping. Recurring tasks are untouched by `clear_reminder`:
  their `reminder` is advanced to the next cycle by pass 2 / the callback
  handlers instead.
- **Pass 2 advances a recurring reminder in PG *before* sending the late
  «⏰ Пропущено» notification** (#206 follow-up). If the process is
  auto-reloaded (frequent deploys → auto-pull + watchfiles) between the
  `send_message` and the `set_props` that moves the reminder forward, the
  next pass 2 would see the reminder still in the past and send «⏰ Пропущено»
  again — the same duplication `clear_reminder` fixed for one-off reminders.
  Ordering the PG advance first makes it idempotent across restarts; the
  cost is losing a single missed-notification if `send_message` then fails
  (the next cycle still pings).
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
    task completion from the bot: non-recurring `Done`
    (`_update_streak_line`, `source="bot_task_done"`), recurring deadline-done
    (`_handle_recurring_deadline_done`, `source="bot_recurring_done"`), and
    recurring reminder-done (`_handle_recurring_reminder_done`,
    `source="bot_recurring_reminder_done"`); plus the Mini App
    (`source="miniapp_task_done"`). Same-day repeat calls are no-ops
    (idempotent on `last_activity_date`). It has **no lazy decay** — the
    counter only drops inside `update_streak` when the last activity is
    older than yesterday (no `reset_broken_streaks` for the global table).
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
  per-task/global streak update; `POST /api/tasks` create is
  idempotency-guarded for the offline write queue — #189 / ADR-0025),
  `miniapp/backend/routes/streaks.py`
  (`reset_broken_streaks` + `get_user_task_streaks`).

## Model routing (from code)

`nexus/handlers/tasks.py` uses Haiku exclusively
(`claude-haiku-4-5-20251001`): parsing date/priority/category/repeat from
free text and short ADHD advice lines. No Sonnet, no Opus in the tasks path.
Reads/writes/status/streak logic are pure SQL/SQLite — no LLM. Deferred-expense
category resolution on completion also runs on Haiku
(`finance.expense_from_task_note`).

## Verify against code

- `alembic/versions/h8c9d0e1f2a3_nexus_tasks_pg.py` — tables + seeded codes
- `alembic/versions/cd34ef56a1b2_drop_dead_notion_id_columns.py` — notion_id dropped (#149)
- `alembic/versions/df56a1b2c3d4_rename_user_notion_id_to_user_id.py` — user_notion_id → user_id (#144)
- `alembic/versions/a7b8c9d0e1f2_tasks_note.py` — `note` column
- `nexus/repos/tasks_tables.py` — SQLAlchemy Core definitions
- `nexus/repos/pg_tasks_repo.py` — `Task` dataclass, lookup cache, `_match`,
  create/status/props/repeat, reminder-restore queries, `clear_reminder` (#206),
  `active_with_future_deadline_no_reminder` (#212)
- `nexus/repos/tasks_repo.py` — repository seam (`clear_reminder`)
- `nexus/handlers/tasks.py` — create/complete/recurring reset
  (`_handle_recurring_task_reset`, `_handle_recurring_reminder_done`),
  `restore_reminders_on_startup` (`periodic` param — #210; pass 4 deadline
  re-arm — #212), `_schedule_reminder`/`_schedule_deadline_check` (`recipients`
  fan-out — #211), `_parse_repeat_time`, `_reschedule_all_for_tz`,
  `_update_streak_line`, Haiku `ask_claude` calls
- `nexus/nexus_bot.py` — `reminder_resync` interval job (90 s, #210)
- `core/classifier.py` — `build_system` future-task-money → `note` rule
- `nexus/handlers/finance.py` — `expense_from_task_note`, `_write_one_time_expense`
- `core/task_streaks.py` — per-task streak store + rules
- `nexus/handlers/streaks.py` — global daily streak
- `core/subtasks_handler.py` — "📋 Подзадачи" factory router → 🗒️ Списки
- `miniapp/backend/routes/tasks.py` — `GET /api/tasks`
- `miniapp/backend/routes/writes.py` — completion write + streak updates
- `miniapp/backend/routes/streaks.py` — per-task streak read/reset
