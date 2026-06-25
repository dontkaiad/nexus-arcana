# SESSIONS — data-model contract (🃏 Расклады)

Code conforms to: a0b0f64. This spec describes the sessions (tarot spreads)
data model as of that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

🃏 Расклады (sessions) are tarot readings: a question, the drawn cards, an
interpretation (authored-expand in mode A, LLM-generated in mode B — see
Processing layer), optional client attribution, local finance (amount/paid),
an outcome ("сбылось"), and an optional Cloudinary photo.

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
| `interpretation` | Text | reading text (authored-expand mode A, or Sonnet mode B) |
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
| `work_id` | BigInteger | FK → `works.id` (ON DELETE SET NULL, indexed; #151) |
| `archived` | Boolean | default false — soft-delete |
| `created_at` / `updated_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `idx_sessions_client_id`, `idx_sessions_occurred_at`,
`idx_sessions_user`, `idx_sessions_work_id`. No `notion_id` column.

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
- **Interpretation provenance depends on mode** (see Processing layer).
  Mode A: the practitioner dictates terse accents; Sonnet expands them into
  prose grounded in the deck reference. Mode B: Sonnet generates the reading
  from the deck reference, memory, and past sessions. `triplet_summary` is a
  Haiku-generated 1–2-sentence digest used for RAG indexing and the Mini App
  summary call.
- **Outcome uses `session_outcome`** (own lookup), distinct from rituals.
- **Barter is Arcana-only**: `payment_source` code `barter` (🔄 Бартер) +
  `barter_what`; consistent with FINANCE.md / LISTS.md.
- **`photo_url` is a Cloudinary URL** (upload via `core/cloudinary_client.py`).

## Processing layer

### Card parsing pipeline

The handler receives a free-text transcript from voice/text input. Card
names go through two mutually exclusive paths based on `deck`:

**Rider-Waite deck** (`_gr_deck == "rider-waite"`, `:1025`):
`core/waite_cards.normalize_waite_cards_in_data` — deterministic
exact-match + fuzzy-match pipeline over a closed 78-card reference
(ADR-0013). No LLM involved; output is canonical English (`"Queen of
Swords"`). Haiku fallback fires only for truly unrecognised misheards that
the deterministic layer cannot resolve.

**Authored / other decks** (all else, `:1036`):
`core/card_grounding.ground_cards_in_data` with a `resolver` that calls
`find_card(deck, text)` to get the canonical RU name. SequenceMatcher grounding
catches near-miss misheards within the transcript; `_canon_ru` then
canonicalises every card and bottom-card field to the deck's RU spelling
after grounding.

Both paths log the before/after diff (`"🔍 карты: old→new; …"`) to the ops
group via `core/bot_notify.notify_log_group`.

### Interpretation modes

Mode is determined at `:1157`: `authored = (data.get("interpretation") or
"").strip()`. If non-empty → **mode A**; if empty but cards present →
**mode B**.

**Mode A — authored expand** (`_polish_authored_interpretation`, `:600`):
The practitioner dictated terse per-card accents. Sonnet expands them into
full prose using `PERSONAL_INTERP_SYSTEM` (deck reference as grounding
anchor, hallucination prohibited — only card meanings or Kai's accents as
source). Temperature 0.5 (expansion follows given material). First-person
voice is inviolable. See ADR-0015.

**Mode B — LLM generation** (`:1172`):
No dictated text. Sonnet generates from `TAROT_SYSTEM` extended with:
deck reference (`cards_context`), memory context, prior sessions
(`prev_context`), and a `_rag_voice_block` (voice-consistency block from
semantic search — see RAG indexing below). Temperature 0.7.

### RAG indexing

After a session saves, mode A sessions are indexed into the vector store
(`:874` single-session, `:1508` multi-session):

```
if authored:
    await _rag_index_safe(page_id, cards=..., question=..., interpretation=..., ...)
```

**`core/rag.index_triplet`** upserts into `arcana_triplets` (pgvector table,
migration `v2w3x4y5z6a7_arcana_triplets_pgvector.py`): columns
`triplet_id`, `cards`, `question`, `interpretation`, `client_id`,
`session_name`, `occurred_at`, `embedding` (vector(1024)).
Embedder: Voyage AI `voyage-4-lite`, batch via `core/rag.index_triplets_batch`
(N triplets = 1 Voyage call — 3 RPM free-tier aware). Similarity:
cosine (`1 - (embedding <=> CAST(:q AS vector))`). Index: HNSW.

**Authored gate:** mode B interpretations are never indexed. The gate is
structural, not policy — `if authored:` is the only write path to
`arcana_triplets`. Corpus purity guaranteed regardless of how often mode B
fires (ADR-0015, ADR-0006).

**`_rag_voice_block`** (`:688`, mode B only): semantic search over
`arcana_triplets` for semantically similar past readings across all clients
(`core/rag.search_triplets`). Returns a `"\n\n--- ПОХОЖИЕ ПРОШЛЫЕ РАСКЛАДЫ
---\n…"` block injected into mode B's system prompt for voice consistency.
Graceful — empty string on failure (`:703`).

### Multi-session flow

`_handle_multi_session` (`:1295`) is invoked when the parsed data contains
`triplets` (e.g., `"Тема: 1) вопрос A  2) вопрос B"`). For each triplet:

1. Card parsing (same branch as single-session).
2. Mode A/B determination per triplet.
3. `_make_triplet_summary` → 1–2-sentence digest (Haiku).
4. Session row created via `PgSessionsRepo.create`.
5. If `authored`: appends `{cards, question, interpretation, …}` dict to
   `rag_batch`.

After the loop, one batch embed call: `core/rag.index_triplets_batch(rag_batch)`
(N triplets = 1 Voyage call). `session_name` slug is Haiku-generated once
per multi-session to group all rows under one name.

### `_make_triplet_summary`

`arcana/handlers/sessions.py:_make_triplet_summary` (`:573`):
Haiku `claude-haiku-4-5-20251001`, max_tokens 160, temp 0.5. Prompt:
question + cards + bottom + interpretation[:1500] → 1–2-sentence digest.
Output sanitised via `core/html_sanitize.sanitize_summary`. Empty string on
any failure (non-blocking).

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

- Haiku (`claude-haiku-4-5-20251001`) — spread parsing from free text;
  `_make_triplet_summary` (1–2-sentence digest); `session_name` slug
  generation in `_handle_multi_session`.
- Sonnet (`config.model_sonnet`) — tarot interpretation in both modes
  (mode A: `_polish_authored_interpretation`, temp 0.5; mode B: open
  generation, temp 0.7); Mini App session summary
  (`miniapp/backend/routes/arcana_sessions.py:summarize`). Sonnet is
  justified for narrative/empathetic readings requiring coherent prose.
- Voyage AI (`voyage-4-lite`) — batch embedding of triplets for RAG indexing
  (`core/rag.py:_embed`), not a Claude model.

## Verify against code

- `alembic/versions/a1f2e3d4c5b6_sessions_slice_schema.py` — table + `session_outcome`
- `alembic/versions/f6a7b8c9d0e1_sessions_add_barter_what.py` — `barter_what`
- `alembic/versions/n4g5h6i7j8k9_sessions_photo_url.py` — `photo_url`
- `alembic/versions/v2w3x4y5z6a7_arcana_triplets_pgvector.py` — `arcana_triplets` table (RAG)
- `arcana/repos/sessions_tables.py` — SQLAlchemy Core mirror (shared lookups)
- `arcana/repos/pg_sessions_repo.py` — `PgSessionsRepo` (create/update/outcome/archive)
- `arcana/repos/sessions_repo.py` — seam + `Session` object
- `arcana/handlers/sessions.py` — card parsing, modes A/B, RAG gate, multi-flow, summary
- `core/waite_cards.py` — deterministic Waite parser (ADR-0013)
- `core/card_grounding.py` — SequenceMatcher grounding for authored/other decks
- `core/rag.py` — `index_triplet`, `index_triplets_batch`, `search_triplets`
- `core/client_resolve.py` — client resolution on create
- `core/cash_register.py` — P&L reads session rows (see FINANCE.md)
- `miniapp/backend/routes/arcana_sessions.py` — session endpoints + Sonnet summary
