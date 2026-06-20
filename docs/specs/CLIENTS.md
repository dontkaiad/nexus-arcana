# CLIENTS — data-model contract (👥 Клиенты)

Code conforms to: 0bc132e. This spec describes the clients data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

👥 Клиенты is the Arcana practice CRM and the **hub entity** of the Arcana
domain: each session (расклад), ritual, and work links to a client by a
`client_id` foreign key. A client holds identity (name, type, status),
profile (contact, request, notes, birthday), and photos (avatar + object
photos).

## Schema

One table `clients` plus two seeded lookup tables. Migrations:
`alembic/versions/0857b6b83518_clients_slice_schema.py` (creates table +
`client_type`/`client_status`), `d4f5e6a7b8c9_clients_pg_native.py` (drops the
`notion_id` bridge — clients are PG-native), `e5f6a7b8c9d0_clients_add_user_notion_id.py`
(adds `user_notion_id`). SQLAlchemy Core mirror: `arcana/repos/clients_tables.py`.

### `clients`

| Column | Type | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement — the cross-domain `client_id` |
| `name` | Text | NOT NULL |
| `type_id` | SmallInteger | FK → `client_type.id` |
| `status_id` | SmallInteger | FK → `client_status.id` |
| `birthday` | Date | nullable |
| `notes` | Text | nullable |
| `request` | Text | nullable |
| `contact` | Text | nullable |
| `photo_url` | Text | nullable — Cloudinary URL (avatar) |
| `object_photos` | Text | nullable — newline list of `URL | note` |
| `user_notion_id` | Text | nullable |

Indexes (from migrations): `idx_clients_name` (name), `idx_clients_user`
(user_notion_id). There is **no `notion_id` column** — it was dropped when the
clients slice went PG-native (`d4f5e6a7b8c9`); `client_id` is the canonical
key everywhere.

### Lookup tables (`id SMALLINT PK`, `code`, `emoji`, `label`, `sort`)

`client_type`, `client_status`. Codes are seeded in
`0857b6b83518_clients_slice_schema.py` (source of truth). Examples,
non-exhaustive — see the migration:
- `client_type`: `free` (🎁), `paid` (🤝), `self` (🌟) (examples, non-exhaustive — see migration).
- `client_status`: `closed` (⛔), `one_time` (🌙), `active` (🟢) (examples, non-exhaustive — see migration).

### Domain object

`arcana/repos/clients_repo.py:Client` (returned by `PgClientsRepo`); `id` is a
string. The "full" select adds profile/photo fields (`_row_to_client_full`).

## Operations & contract

`PgClientsRepo` (`arcana/repos/pg_clients_repo.py`, `asyncio.to_thread` over
sync SQLAlchemy). Notion-style type/status labels are mapped to codes via
`_type_code`/`_status_code`; lookup ids resolved by `_resolve_lookup`.

- **resolve / find** — `find(name)` matches `name ILIKE %name%`, ordered by
  `id`, limit 1 (lowest-id wins). `find_by_id(pg_id)` returns the full
  profile. `find_self(user_notion_id)` returns the `self`-type client.
- **create (with dedup guard)** — `create(name, type_code, …)` first checks
  `name ILIKE name` (case-insensitive exact) and **returns the existing id if
  found**, else inserts (default `type='paid'`, `status='active'`). This
  guard exists to prevent duplicate clients.
- **resolve-or-create** — `core/client_resolve.py:resolve_or_create` wraps
  `core.notion_client.find_or_create_client(name)`: `find(name)` → existing
  `(id, False)` else `create` → `(str(pg_id), True)` and announces
  "🆕 Создала клиента …". The returned `str(pg_id)` is used as `client_id`
  across sessions/rituals/works.
- **update profile** — `update_profile(...)` sets contact/request/notes/
  birthday/photo_url/object_photos and optionally type. `get_object_photos`
  reads the raw `object_photos` text.
- **list** — `list_all(user_notion_id)` returns full client rows (scoped by
  user when provided).

## Invariants

- **Client resolution is by name, not by id, on the write path.** Records are
  attached to a client resolved from the extracted `client_name`
  (case-insensitive). The create-time `ILIKE name` guard is what prevents the
  historical duplicate-client bug; `find()` uses a broader `ILIKE %name%`
  and returns the lowest id on ambiguity.
- **`client_id` is the single cross-domain key** (`str(clients.id)`). Sessions,
  rituals, and works each carry a `client_id` FK → `clients.id` (see those
  specs). Clients do not back-reference them.
- **Type/status are FK-constrained** to `client_type` / `client_status`.
- **`self`-type client is special**: the practitioner's own client, found by
  `find_self`; excluded from P&L (see `core/cash_register.py`, FINANCE.md).
- **`object_photos` is a serialized text field**, one `URL | note` per line
  (parsed by `core/client_object_photos.py`); photos upload via
  `core/cloudinary_client.py`.

## Lifecycle / status model

```
create → status active ──┐
                          ├─▶ one_time / closed  (client_status codes)
```

Status is an attribute (`client_status`), not a hard lifecycle; there is no
delete/archive method in `PgClientsRepo` — a client persists once created.

## Callers

- Bots — `arcana/handlers/clients.py` (CRM), `arcana/handlers/client_photo.py`
  (avatar + object photos), `core/client_resolve.py` (resolve-or-create used
  by sessions/rituals/work_preview).
- Cross-domain — sessions/rituals/works handlers resolve a client on create;
  `core/cash_register.py` (self-client exclusion).
- Mini App — `miniapp/backend/routes/arcana_clients.py`
  (`GET /api/arcana/clients`, `GET /api/arcana/clients/{client_id}`).

## Model routing (from code)

Client-text parsing (name/type/profile extraction) is Haiku-only
(`claude-haiku-4-5-20251001`, `arcana/handlers/clients.py`). No Sonnet/Opus.
Reads/writes are pure SQL.

## Verify against code

- `alembic/versions/0857b6b83518_clients_slice_schema.py` — table + lookups
- `alembic/versions/d4f5e6a7b8c9_clients_pg_native.py` — notion_id dropped, rituals FK
- `alembic/versions/e5f6a7b8c9d0_clients_add_user_notion_id.py` — user column
- `arcana/repos/clients_tables.py` — SQLAlchemy Core mirror
- `arcana/repos/pg_clients_repo.py` — `PgClientsRepo`, find/create dedup guard, profile
- `arcana/repos/clients_repo.py` — seam + `Client` object
- `core/client_resolve.py` — `resolve_or_create` + announce
- `core/notion_client.py` — `find_or_create_client`, `client_find`
- `core/client_object_photos.py` — `URL | note` serialization
- `core/cloudinary_client.py` — photo upload
- `arcana/handlers/clients.py`, `arcana/handlers/client_photo.py` — handlers
- `miniapp/backend/routes/arcana_clients.py` — client endpoints
