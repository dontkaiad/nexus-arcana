# SESSIONS — data-model contract (🃏 Расклады)

Code conforms to: 596c5ea. This spec describes the sessions (tarot spreads)
data model as of that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

🃏 Расклады (sessions) are tarot readings: a question, the drawn cards, an
LLM-generated interpretation, optional client attribution, local finance
(amount/paid), an outcome ("сбылось"), and an optional Cloudinary photo.

## Schema

One table `sessions` plus the `session_outcome` lookup; it **shares**
`engagement_type` and `payment_source` lookups with rituals. Migrations:
`alembic/versions/a1f2e3d4c5b6_sessions_slice_schema.py` (table + outcome),
`f6a7b8c9d0e1_sessions_add_barter_what.py`, `n4g5h6i7j8k9_sessions_photo_url.py`.
SQLAlchemy Core mirror: `arcana/repos/sessions_tables.py`.

### `sessions`

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `title` | Text | NOT NULL |
| `occurred_at` | Date | nullable |
| `question` | Text | the querent's question |
| `cards` | Text | drawn cards |
| `interpretation` | Text | LLM-generated reading (Sonnet) |
| `triplet_summary` | Text | LLM triplet summary |
| `bottom_card` | Text | "дно колоды" |
| `session_name` | Text | grouping/slug name |
| `spread_type` | Text | spread layout |
| `area` | Text | life area |
| `deck` | Text | deck used |
| `amount` | Numeric(10,2) | default 0 — local finance |
| `paid` | Numeric(10,2) | default 0 — local finance |
| `type_id` | SmallInteger | FK → `engagement_type.id` (shared) |
| `payment_src_id` | SmallInteger | FK → `payment_source.id` (shared) |
| `outcome_id` | SmallInteger | FK → `session_outcome.id` |
| `client_id` | BigInteger | FK → `clients.id` |
| `barter_what` | Text | barter item (Arcana-only concept) |
| `photo_url` | Text | Cloudinary URL |
| `user_notion_id` | Text | owner |
| `archived` | Boolean | default false — soft-delete |
| `created_at` / `updated_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `idx_sessions_client_id`, `idx_sessions_occurred_at`,
`idx_sessions_user`. No `notion_id` column.

### Enumerated lookups

Owned by the migrations (source of truth). Examples, non-exhaustive:
- `session_outcome` (own table): `unverified` (⏳), `partial` (〰️),
  `no` (❌), `yes` (✅) (examples, non-exhaustive — see migration). Note this
  is a **separate** table from rituals' `outcome_status`, with similar codes.
- `spread_type` / `area` / `deck` are free Text (no FK); card names are
  canonicalized elsewhere (`core.preprocess._tarot_card_names_ru`, see
  CLAUDE.md), not by an enum here.

### Domain object

`arcana/repos/sessions_repo.py:Session` (returned by `PgSessionsRepo`).

## Operations & contract

`PgSessionsRepo` (`arcana/repos/pg_sessions_repo.py`):

- **create** — inserts a session; the client is resolved beforehand via
  `core/client_resolve.py` (see Invariants) and passed as `client_id`.
- **read** — `find_by_id`, `list_by_client(client_id)`, `list_all`,
  `search(query)`, `list_by_slug(slug)` (group by `session_name`),
  `canonical_session_name(...)`.
- **content updates** — `update_interpretation(id, text)` (Sonnet output),
  `update_summary(id, …)` (triplet summary), `set_photo_url(id, url)`.
- **outcome** — `set_outcome(id, code)` sets `outcome_id` (the "сбылось"
  state).
- **soft-delete** — `archive(id)` sets `archived = true`.

## Invariants

- **Client attribution by FK.** `client_id` → `clients.id`. A session with an
  extracted `client_name` must resolve through `find_or_create_client`
  (CLIENTS.md); leaving a client-type session without a client relation is
  the "orphan" anti-pattern (CLAUDE.md).
- **Finance is stored locally, not in the ledger.** `amount`/`paid` live on
  the session row; sessions do **not** write to `arcana_pnl`. The P&L
  aggregates session rows separately (see FINANCE.md, `core/cash_register.py`).
- **Interpretation/summary are LLM-generated** and stored in
  `interpretation` / `triplet_summary` (Sonnet — see Model routing).
- **Outcome uses `session_outcome`** (own lookup), distinct from rituals.
- **Barter is Arcana-only**: `payment_source` code `barter` (🔄 Бартер) +
  `barter_what`; consistent with FINANCE.md / LISTS.md.
- **`photo_url` is a Cloudinary URL** (upload via `core/cloudinary_client.py`).

## Lifecycle / status model

```
create → (interpretation + triplet_summary generated) → outcome set (сбылось?) → archived
```

`archived` hides a session (soft-delete); there is no hard delete in the
sessions repo. Outcome can be revised via `set_outcome`.

## Callers

- Bot — `arcana/handlers/sessions.py` (parse, interpret, save, outcome,
  photo), `arcana/handlers/reply_update.py` (reply-based edits).
- Cross-domain — `core/client_resolve.py` (client), `core/cash_register.py`
  (P&L), `core/work_relation.py` (Notion-era Работа↔Расклад relation; see
  WORKS.md for the PG-native picture).
- Mini App — `miniapp/backend/routes/arcana_sessions.py`
  (`GET /api/arcana/sessions`, `…/by-slug/{slug}`, `…/by-slug/{slug}/summarize`,
  `GET …/{session_id}`).

## Model routing (from code)

- Haiku (`claude-haiku-4-5-20251001`) — parsing the spread from free text
  (`arcana/handlers/sessions.py`).
- Sonnet (`config.model_sonnet`) — tarot interpretation, triplet summary, and
  the Mini App session summary (`arcana/handlers/sessions.py` multiple calls;
  `miniapp/backend/routes/arcana_sessions.py:summarize`). Sonnet is justified
  for narrative/empathetic readings.

## Verify against code

- `alembic/versions/a1f2e3d4c5b6_sessions_slice_schema.py` — table + `session_outcome`
- `alembic/versions/f6a7b8c9d0e1_sessions_add_barter_what.py` — `barter_what`
- `alembic/versions/n4g5h6i7j8k9_sessions_photo_url.py` — `photo_url`
- `arcana/repos/sessions_tables.py` — SQLAlchemy Core mirror (shared lookups)
- `arcana/repos/pg_sessions_repo.py` — `PgSessionsRepo` (create/update/outcome/archive)
- `arcana/repos/sessions_repo.py` — seam + `Session` object
- `arcana/handlers/sessions.py` — parse (Haiku) + interpretation/summary (Sonnet)
- `core/client_resolve.py` — client resolution on create
- `core/cash_register.py` — P&L reads session rows (see FINANCE.md)
- `miniapp/backend/routes/arcana_sessions.py` — session endpoints + Sonnet summary
