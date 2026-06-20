# RITUALS — data-model contract (🕯 Ритуалы)

Code conforms to: 0bc132e. This spec describes the rituals data model as of
that commit; update it in the same PR that changes the model.

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
- **result/outcome** — `set_result(...)` records the outcome (`outcome_id`).
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
  inventory rows.
- **Outcome uses `outcome_status`** (distinct from sessions' `session_outcome`).
- **Barter is Arcana-only**: `payment_source` code `barter` + `barter_what`.
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
  `arcana/handlers/ritual_writeoff.py` (inventory write-off),
  `arcana/handlers/barter_prompt.py` (barter), `arcana/handlers/reply_update.py`.
- Cross-domain — `core/client_resolve.py` (client), `core/cash_register.py`
  (P&L), `core/work_relation.py` (Notion-era Работа↔Ритуал; see WORKS.md).
- Mini App — `miniapp/backend/routes/arcana_rituals.py`
  (`GET /api/arcana/rituals`, `GET …/{ritual_id}`).

## Model routing (from code)

Ritual-text parsing is Haiku-only (`claude-haiku-4-5-20251001`,
`arcana/handlers/rituals.py`). No Sonnet/Opus in the rituals path.
Reads/writes are pure SQL.

## Verify against code

- `alembic/versions/022e99f6431d_rituals_slice_schema.py` — table, lookups, `ritual_debt` view
- `alembic/versions/d4f5e6a7b8c9_clients_pg_native.py` — `fk_rituals_client_id`
- `arcana/repos/rituals_tables.py` — SQLAlchemy Core mirror
- `arcana/repos/pg_rituals_repo.py` — `PgRitualsRepo` (create/result/delete/photo)
- `arcana/repos/rituals_repo.py` — seam + `Ritual` object
- `arcana/handlers/rituals.py` — parse (Haiku) + save/result
- `arcana/handlers/ritual_writeoff.py` — inventory decrement (operational link)
- `core/client_resolve.py` — client resolution on create
- `core/cash_register.py` — P&L reads ritual rows (see FINANCE.md)
- `miniapp/backend/routes/arcana_rituals.py` — ritual endpoints
