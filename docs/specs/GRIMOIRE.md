# GRIMOIRE — data-model contract (📖 Гримуар)

Code conforms to: 596c5ea. This spec describes the grimoire data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

📖 Гримуар is the practice's **domain knowledge base**: spells, recipes,
combinations, and notes. It is a reference library about the *craft* — not
about the user. Per the memory boundary (MEMORY.md / ADR-0005), domain
knowledge like the grimoire lives in its own table and is explicitly **not**
personal memory.

## Schema

One table `grimoire_entries` plus one seeded lookup table. Migration:
`alembic/versions/c3f4e5d6a7b8_grimoire_slice_schema.py`. SQLAlchemy Core
mirror: `arcana/repos/grimoire_tables.py`.

### `grimoire_entries`

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `title` | Text | NOT NULL |
| `category_id` | SmallInteger | FK → `grimoire_category.id` |
| `themes` | Text | comma-separated display labels (free text) |
| `verified` | Boolean | default false |
| `text` | Text | the entry body |
| `source` | Text | provenance |
| `user_notion_id` | Text | owner |
| `created_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `idx_grimoire_category_id`, `idx_grimoire_user`. No `notion_id`
column. There is **no `updated_at`** column on this table.

### Enumerated lookup & themes

Owned by `c3f4e5d6a7b8_grimoire_slice_schema.py` (source of truth). Examples,
non-exhaustive:
- `grimoire_category`: `spell` (📿), `recipe` (🧴), `combo` (✨), `note` (📝) (examples, non-exhaustive — see migration).
- `themes` is **free comma-separated text** (display labels), not an FK or a
  relation table.

### Domain object

`arcana/repos/grimoire_repo.py:GrimoireEntry` (returned by `PgGrimoireRepo`).

## Operations & contract

`PgGrimoireRepo` (`arcana/repos/pg_grimoire_repo.py`):

- **add** — `add(title, category, themes, text, source, …)` inserts an entry.
- **read** — `list_by_category(category)`, `search(query)`, `list_all`,
  `find_by_id(id)`.

The repo exposes no update or delete method — within this contract a grimoire
entry is append-and-read.

## Invariants

- **Grimoire is domain knowledge, not memory.** It is a standalone reference
  table with no FK to clients, sessions, rituals, or works; it is the natural
  RAG source for domain knowledge, not the personal-memory store (MEMORY.md).
- **Category is FK-constrained** to `grimoire_category`; `themes` is free
  comma-separated text.
- **`verified` is a quality flag** on the entry (default false), independent
  of any lifecycle.
- **Scoped by `user_notion_id`** for ownership; reads filter by it when
  provided.

## Lifecycle / status model

No status lifecycle. An entry is created (`add`) and read; `verified` may be
set as a quality marker. There is no archive/delete path in the repo.

## Callers

- Bot — `arcana/handlers/grimoire.py` (parse + save + browse).
- Mini App — `miniapp/backend/routes/arcana_grimoire.py`
  (`GET /api/arcana/grimoire`, `GET …/{entry_id}`).

## Model routing (from code)

Grimoire-text parsing is Haiku-only (`claude-haiku-4-5-20251001`,
`arcana/handlers/grimoire.py`). No Sonnet/Opus. Reads/writes are pure SQL.

## Verify against code

- `alembic/versions/c3f4e5d6a7b8_grimoire_slice_schema.py` — table + `grimoire_category`
- `arcana/repos/grimoire_tables.py` — SQLAlchemy Core mirror
- `arcana/repos/pg_grimoire_repo.py` — `PgGrimoireRepo` (add/list/search/find)
- `arcana/repos/grimoire_repo.py` — seam + `GrimoireEntry` object
- `arcana/handlers/grimoire.py` — Haiku parse + save/browse
- `miniapp/backend/routes/arcana_grimoire.py` — grimoire endpoints
- `docs/specs/MEMORY.md` / `docs/CASES/0005-memory-store.md` — domain-knowledge-vs-memory boundary
