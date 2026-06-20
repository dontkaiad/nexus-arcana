# WORKS — data-model contract (🔮 Работы)

Code conforms to: 0bc132e. This spec describes the works data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

🔮 Работы are practice work items — a task-like backlog for the Arcana
practice (a planned reading, ritual, or other job), each with a priority,
status, optional deadline/reminder, and optional client attribution. Works is
its **own entity**, not an aggregator over session/ritual rows (see
Invariants).

## Schema

One table `works` plus two seeded lookup tables. Migrations:
`alembic/versions/b2f3e4d5c6a7_works_slice_schema.py` (table +
`work_priority`/`work_status`), `g7b8c9d0e1f2_works_add_reminder.py` (adds
`reminder`), `o5h6i7j8k9l0_works_add_archived_status.py` (adds the `archived`
status code). SQLAlchemy Core mirror: `arcana/repos/works_tables.py`.

### `works`

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `title` | Text | NOT NULL |
| `deadline` | TIMESTAMP(tz) | nullable |
| `category` | Text | free-text label (see Invariants) |
| `priority_id` | SmallInteger | FK → `work_priority.id` |
| `status_id` | SmallInteger | FK → `work_status.id` |
| `client_id` | BigInteger | FK → `clients.id` |
| `reminder` | TIMESTAMP(tz) | nullable |
| `user_notion_id` | Text | owner |
| `created_at` / `updated_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `idx_works_status_id`, `idx_works_deadline`, `idx_works_client_id`.
No `notion_id` column.

### Enumerated lookups & category

Owned by the migrations (source of truth). Examples, non-exhaustive:
- `work_priority`: `urgent` (🔴), `important` (🟡), `later` (🟢) (examples, non-exhaustive — see migration).
- `work_status`: `open` (🔵), `done` (✅), `archived` (🗄️, added in `o5h6i7j8k9l0`) (examples, non-exhaustive — see migrations).
- `category` is a **free Text label** (not an FK), e.g. `🃏 Расклад`,
  `✨ Ритуал` (examples, non-exhaustive — owned by the handlers). It
  categorizes the work; it does not point at a session/ritual row.

### Domain object

`arcana/repos/works_repo.py:Work` (returned by `PgWorksRepo`).

## Operations & contract

`PgWorksRepo` (`arcana/repos/pg_works_repo.py`):

- **create** — inserts a work; client (if any) resolved beforehand via
  `core/client_resolve.py` and passed as `client_id`.
- **read** — `list_open(user_notion_id)`, `find_by_id`, `list_all`.
- **status** — `set_status(id, code)`, `mark_done(id)` (status → `done`).
- **schedule** — `set_deadline(id, …)`; `reminder` drives APScheduler jobs
  (the shared reminder flow, `core/reminder_scheduler.py`).

## Invariants

- **Work↔event link is a PG FK on the event, 1:1 (#151).** `sessions` and
  `rituals` each carry a nullable `work_id` → `works.id` (`ON DELETE SET NULL`,
  indexed; migration `s9t0u1v2w3x4`). `works` itself holds no session/ritual
  reference — the link is owned by the event row, not the work. Creating a
  session/ritual finds the one open Work for that client+category, stamps
  `work_id`, and closes the Work — in PG, via `core/work_relation.py` (no Notion).
  A junction table was rejected because the cardinality is 1:1 (see ARCHITECTURE.md).
- **`engagement_type` (client/personal) is NOT on works.** That lookup lives
  on `sessions`/`rituals` (`type_id`). A work carries only `priority`/`status`
  plus a `category` label; client attribution is via `client_id` only.
- **Client link is an FK** (`client_id` → `clients.id`); resolved by name on
  create (see CLIENTS.md).
- **Lists reference works loosely.** `nexus_lists`/`arcana_inventory` store a
  `works_id` page-id **string** (Notion-era id), not a PG FK (see LISTS.md).
- **Priority/status are FK-constrained** to `work_priority` / `work_status`.

## Lifecycle / status model

```
create → status open ──mark_done──▶ done
                     └──set_status──▶ archived
```

Status is FK-backed (`work_status`); `archived` is a status code (added in
`o5h6i7j8k9l0`), not a separate boolean. `deadline`/`reminder` are scheduling
attributes; reminder jobs are derived from the columns, not stored.

## Callers

- Bot — `arcana/handlers/works.py`, `arcana/handlers/work_preview.py`
  (preview-then-save flow), `arcana/handlers/work_kb.py` (inline keyboards).
- Cross-domain — `core/client_resolve.py` (client),
  `core/work_relation.py` (Notion-era auto-relation + auto-close; see #151),
  `core/reminder_scheduler.py` (reminders).
- Mini App — (works are surfaced via Arcana today/aggregate routes;
  `miniapp/backend/routes/arcana_today.py`).

## Model routing (from code)

Work-text parsing is Haiku-only (`claude-haiku-4-5-20251001`,
`arcana/handlers/works.py`, `arcana/handlers/work_preview.py`). No Sonnet/Opus.
Reads/writes are pure SQL.

## Verify against code

- `alembic/versions/b2f3e4d5c6a7_works_slice_schema.py` — table + lookups
- `alembic/versions/g7b8c9d0e1f2_works_add_reminder.py` — `reminder`
- `alembic/versions/o5h6i7j8k9l0_works_add_archived_status.py` — `archived` status
- `arcana/repos/works_tables.py` — SQLAlchemy Core mirror
- `arcana/repos/pg_works_repo.py` — `PgWorksRepo` (create/status/deadline/mark_done)
- `arcana/repos/works_repo.py` — seam + `Work` object
- `arcana/handlers/works.py`, `arcana/handlers/work_preview.py` — Haiku parse + preview
- `core/work_relation.py` — Notion-era session/ritual → work relation (#151)
- `arcana/repos/sessions_tables.py`, `arcana/repos/rituals_tables.py` — confirm no `works_id`
- `core/client_resolve.py` — client resolution on create
