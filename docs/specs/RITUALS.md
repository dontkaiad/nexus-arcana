# RITUALS — data-model contract (🕯 Ритуалы)

Code conforms to: 0bc132e (+ #7/#8: Mini App finance serialization, shared
`source_label`; + #8: `consumables_written_off` timestamp; + #10: `work_id`
reverse-link surfaced in Mini App). This spec describes the rituals data
model; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

🕯 Ритуалы are magical works: a structured rite with a purpose, place,
offerings/consumables, local finance (price/paid), an outcome, optional client
attribution, and an optional Cloudinary photo.

## Schema

One table `rituals` plus five seeded lookup tables; `payment_source` and
`engagement_type` are **shared** with sessions. Migration:
`alembic/versions/022e99f6431d_rituals_slice_schema.py` (the base Arcana
migration; also seeds the shared lookups and the `ritual_debt` view). The
`rituals.client_id` FK to `clients.id` is added in
`d4f5e6a7b8c9_clients_pg_native.py` (`fk_rituals_client_id`, ON DELETE SET
NULL). SQLAlchemy Core mirror: `arcana/repos/rituals_tables.py`.

### `rituals`

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `title` | Text | NOT NULL |
| `occurred_at` | TIMESTAMP(tz) | nullable |
| `client_id` | BigInteger | FK → `clients.id` (ON DELETE SET NULL) |
| `payment_src_id` | SmallInteger | FK → `payment_source.id` (shared) |
| `type_id` | SmallInteger | FK → `engagement_type.id` (shared) |
| `purpose_id` | SmallInteger | FK → `magical_purpose.id` |
| `outcome_id` | SmallInteger | FK → `outcome_status.id` |
| `place_id` | SmallInteger | FK → `ritual_place.id` |
| `price` | Numeric(10,2) | local finance |
| `paid` | Numeric(10,2) | default 0 — local finance |
| `offerings_sum` | Numeric(10,2) | offerings cost |
| `duration_min` | Integer | |
| `photo_url` | Text | Cloudinary URL |
| `forces` | Text | invoked forces |
| `structure` | Text | rite structure |
| `consumables` | Text | free text (see Invariants) |
| `offerings` | Text | |
| `barter_what` | Text | barter item (Arcana-only) |
| `notes` | Text | |
| `work_id` | BigInteger | FK → `works.id` (ON DELETE SET NULL, indexed; #151) |
| `consumables_written_off` | TIMESTAMP(tz) | nullable — set when the inventory write-off is confirmed (#8, migration `f1a2b3c4d5e6`); NULL = not written off |
| `archived` | Boolean | NOT NULL default false — soft-archive |
| `created_at` / `updated_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `idx_rituals_client_id`, `idx_rituals_occurred_at`, `idx_rituals_work_id`.
No `notion_id` column.

`ritual_debt` is a **computed SQL view** (`debt = COALESCE(price,0) −
COALESCE(paid,0)`), created in the migration — debt is never stored.

### Enumerated lookups

Owned by `022e99f6431d_rituals_slice_schema.py` (source of truth). Examples,
non-exhaustive:
- `payment_source` (shared): `barter` (🔄), `cash` (💵), `card` (💳).
- `engagement_type` (shared): `client` (🤝), `personal` (🌟).
- `magical_purpose`: e.g. `love_bind` (🔗), `protect` (🛡️), `cleanse` (🧹) (examples, non-exhaustive — see migration).
- `outcome_status`: `unverified` (⏳), `partial` (〰️), `negative` (❌), `positive` (✅) (examples, non-exhaustive — see migration).
- `ritual_place`: e.g. `home` (🏠), `forest` (🌲), `crossroad` (🛤️) (examples, non-exhaustive — see migration).

### Domain object

`arcana/repos/rituals_repo.py:Ritual` (returned by `PgRitualsRepo`).

## Operations & contract

`PgRitualsRepo` (`arcana/repos/pg_rituals_repo.py`):

- **create** — inserts a ritual; the client (if any) is resolved beforehand
  via `core/client_resolve.py` and passed as `client_id`.
- **read** — `find_by_id`, `list_by_client(client_id)`, `list_all`.
  `find_by_id` additionally resolves `work_id` → the linked Work's title via a
  separate `SELECT` (`_work_title`; `works` is **not** joined into
  `_select_rituals` so rituals-only test schemas stay independent of the
  `works → clients` FK). `work_title` is `None` on list reads.
- **result/outcome** — `set_result(...)` records the outcome (`outcome_id`).
- **write-off** — `mark_consumables_written_off(id)` stamps
  `consumables_written_off` (called from `ritual_writeoff.cb_apply` on ✅, #8).
- **photo** — `update_photo_url(id, url)`.
- **delete** — `delete(id)` hard-deletes the row.

## Invariants

- **Debt is derived, never stored.** Read `ritual_debt` (`price − paid`); no
  debt column exists.
- **Finance is stored locally, not in the ledger.** `price`/`paid`/
  `offerings_sum` live on the ritual row; rituals do **not** write to
  `arcana_pnl`. The P&L aggregates ritual rows separately (FINANCE.md,
  `core/cash_register.py`).
- **Client link is FK with SET NULL** (`fk_rituals_client_id`): deleting a
  client nulls the ritual's `client_id` rather than cascading.
- **`consumables` is a free-text field, not a relation to inventory.** The
  practice inventory (`arcana_inventory`, see LISTS.md) is decremented
  operationally by `arcana/handlers/ritual_writeoff.py` after a ritual; that
  write-off is an application flow, **not** a schema FK from `rituals` to
  inventory rows. The only trace on the ritual is the
  `consumables_written_off` timestamp (#8) — a "was it done" flag, not a list
  of what moved.
- **`work_id` is the reverse of the plan.** A *planned* ritual lives in
  `works` (category `✨ Ритуал`); a row in `rituals` is always a *performed*
  rite. On save, `core/work_relation.py` finds the one open Work for that
  client+category, stamps `rituals.work_id`, and closes the Work (#151).
  Mini App surfaces this as `from_work` on the ritual card.
- **Outcome uses `outcome_status`** (distinct from sessions' `session_outcome`).
- **Barter is Arcana-only**: `payment_source` code `barter` + `barter_what`.
- **`payment_source` display label** comes from `core.payment.source_label`
  (shared with sessions) — `_row_to_ritual` maps `payment_code` → label.
- **`photo_url` is a Cloudinary URL** (`core/cloudinary_client.py`).

## Lifecycle / status model

```
create → outcome set (set_result: сработал?) → [delete]
```

Rituals expose **both** a hard `delete` and a soft `archive` (the `archived`
flag, default false; archived rows drop out of `list_all`/`list_by_client` but
stay findable by id). Outcome can be revised via `set_result`.

## Callers

- Bot — `arcana/handlers/rituals.py` (parse/save/result),
  `arcana/handlers/ritual_writeoff.py` (inventory write-off; on ✅ also calls
  `mark_consumables_written_off`, #8),
  `arcana/handlers/barter_prompt.py` (barter), `arcana/handlers/reply_update.py`.
- Cross-domain — `core/client_resolve.py` (client), `core/cash_register.py`
  (P&L), `core/work_relation.py` (Notion-era Работа↔Ритуал; see WORKS.md).
- Mini App — `miniapp/backend/routes/arcana_rituals.py`
  (`GET /api/arcana/rituals`, `GET …/{ritual_id}`). Both list and card
  serialize finance as `price` / `paid` / `debt` (`price − paid`, 0-floored)
  / `source` (payment_source label) / `barter_what` — parity with sessions (#7/#8).
  The card additionally serializes `from_work` (`{id, title}` or `null`, #10)
  and `consumables_written_off` (bool, #8); the list omits `from_work` (no
  `work_title` on list reads).

## Model routing (from code)

Ritual-text parsing is Haiku-only (`claude-haiku-4-5-20251001`,
`arcana/handlers/rituals.py`). No Sonnet/Opus in the rituals path.
Reads/writes are pure SQL.

## Verify against code

- `alembic/versions/022e99f6431d_rituals_slice_schema.py` — table, lookups, `ritual_debt` view
- `alembic/versions/d4f5e6a7b8c9_clients_pg_native.py` — `fk_rituals_client_id`
- `alembic/versions/f1a2b3c4d5e6_rituals_consumables_written_off.py` — `consumables_written_off` (#8)
- `arcana/repos/rituals_tables.py` — SQLAlchemy Core mirror
- `arcana/repos/pg_rituals_repo.py` — `PgRitualsRepo` (create/result/delete/photo)
- `arcana/repos/rituals_repo.py` — seam + `Ritual` object
- `arcana/handlers/rituals.py` — parse (Haiku) + save/result
- `arcana/handlers/ritual_writeoff.py` — inventory decrement (operational link)
- `core/client_resolve.py` — client resolution on create
- `core/cash_register.py` — P&L reads ritual rows (see FINANCE.md)
- `miniapp/backend/routes/arcana_rituals.py` — ritual endpoints
- `core/payment.py` — `source_label` (shared payment_source label helper)
