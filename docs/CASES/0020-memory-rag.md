# ADR-0020 — RAG search over 🧠 Память (shared memory, both bots)

- **Status:** Accepted
- **Date:** 2026-08-21
- **Relates to:** ADR-0005 (memory store), ADR-0006 (RAG vector backend —
  first pgvector consumer), ADR-0016 (single-writer location)
- **Code conforms to:** `core/memory_rag.py`, `core/memory.py`,
  `core/repos/pg_memory_repo.py`, Alembic migration
  `y5z6a7b8c9d0_memories_embedding_pgvector.py`,
  `scripts/migrate_memory_embeddings.py`
- Update this ADR in the same PR that changes the memory-RAG schema or
  search strategy.

## Context

Memory search (`/memory`, LLM context-injection via
`get_memories_for_context`, alias resolution) has always been plain `ILIKE`
substring matching plus a hand-rolled Russian suffix-stripper
(`_tokenize_hint`) — no semantic layer. Case-declined forms, paraphrases,
and synonyms that don't share a literal substring are invisible to search.

The gap surfaced concretely: a user mentioned a small town whose
case-declined form wasn't in `core/location.py`'s hardcoded `CITY_TZ`
dictionary. The lookup failed, fell through to a free-form LLM fallback,
which mis-read the town name as an unrelated word and wrote it to memory
under a category outside the canonical set. A second, independently-firing
code path (`core/classifier.py`'s `timezone_update` handler) *also* wrote a
raw-text memory entry unconditionally, re-parsing the same message from
scratch with no awareness it had already been handled — producing a second,
uncoordinated garbage fact. Both are now fixed as small, separate patches
(see Decision), but they're symptomatic of the same underlying weakness:
a keyword/substring-only memory store is fragile exactly where users phrase
things in ways the store's literal vocabulary doesn't cover.

RAG already exists for Arcana tarot triplets (ADR-0006, `core/rag.py`,
Voyage `voyage-4-lite`/1024, pgvector, table `arcana_triplets`) — one
consumer so far. This ADR extends the same infrastructure to the shared
`memories` table, used by both bots.

## Decision

**Schema: a column on `memories`, not a mirror table.** `arcana_triplets`
is a derived read-model over `sessions` (bulk import + live rows, its own
upsert key). A memory row is already the atomic unit — one row is one fact,
and `upsert`/`archive`/`is_current` already mutate it in place. A second
`memory_embeddings` table would need its own upsert-by-`memory_id` logic
and carries real sync-drift risk (row updated, embedding row stale) for no
benefit. `ALTER TABLE memories ADD COLUMN embedding vector(1024)` wins.

**Extension ownership.** `arcana_triplets`'s migration originally dropped
the `vector` extension on downgrade, assuming sole ownership. Now that
`memories.embedding` is a second consumer, that `DROP EXTENSION` was
removed from the older migration's `downgrade()`; the new migration
`CREATE EXTENSION IF NOT EXISTS vector` (idempotent) and does not drop it
on its own downgrade either — extension lifecycle is no longer owned by
either single table.

**Indexing hook: inside `PgMemoryRepo.add`/`.upsert`, not `save_memory()`
and not per-handler.** The first cut of this change hooked
`core/memory.py:save_memory()` — but that's not actually the single write
path: `save_parsed` (auto-suggest confirm, both bots' `cb_*_auto_yes`
callbacks), the budget writer (`nexus/handlers/finance.py:_save_memory_entry`),
and `core/location.py:set_user_location` all write memory rows directly
through the repo, bypassing `save_memory()` entirely — those rows would
have had no embedding, and any that get `upsert`-updated later would keep
a *stale* one for the old fact text (see #186). Moving the hook one layer
down, into `PgMemoryRepo.add`/`.upsert` themselves, is the actual single
choke point every writer passes through. A fire-and-forget `asyncio` task
(`_spawn_index`, tracked in a module-level set so it isn't GC'd mid-flight)
embeds `fact_text + related_to + category` (category/related_to add real
retrieval signal; the machine-slug `key_name` is excluded as noise) without
the caller awaiting it — the write and the user-facing reply never wait on
Voyage's round-trip. On the `upsert`-update path specifically, the row's
`embedding` is first reset to `NULL` in its own try/except'd
transaction *before* the reindex task fires — belt-and-suspenders against
the stale-vector case, and deliberately tolerant of the embedding column
not existing yet (a fresh checkout that hasn't run the migration): that
reset failing logs a warning and leaves the already-committed fact update
untouched, rather than risking a rollback of the primary write over a
schema mismatch. This differs from Arcana's own `_rag_index_safe()`
wrapper pattern (called explicitly at each sessions.py call site) — that
pattern only exists there because sessions are Arcana-only; the memory
repo is the actual shared choke point, so every writer gets indexing
without needing to call anything.

**Search: hybrid, ILIKE first, semantic only as a thin fallback.** Every
search path (`search_memory`, `get_memories_for_context` via
`_find_pages_by_hint`) runs the existing ILIKE query first; a semantic
query only fires when ILIKE returns fewer than 3 hits, merged in
(ILIKE-first, deduped by id, capped at the existing page size). Always-on
semantic search was rejected on three grounds, not just rate limits: ILIKE
is a local query with no network round-trip, an exact substring match is
almost always the best possible match when one exists, and
`get_memories_for_context` can fire several times per single incoming
Telegram message (once per extracted keyword) — without a payment method
on the Voyage account, the free tier is a hard 3 RPM, which always-on
would burn through on one message alone (shared with `arcana_triplets`,
the other RAG consumer). That rate ceiling lifts substantially (Tier 1:
2000 RPM / 8M TPM) once a payment method is on file — Voyage's free
200M-token allowance still applies, so this isn't a cost decision — but
the first two grounds (latency, match quality) hold regardless of tier.
Three callers opt out of the semantic fallback entirely (`use_semantic=False`):

- the recursive alias resolver (`_resolve_alias`) — alias recall needs to
  stay exact; a "close enough" semantic match at each recursion level
  risks silently renaming the wrong entity;
- `deactivate_memory` ("забудь X") — it deactivates *every* found row
  without confirmation, and the semantic query has no similarity
  threshold (see #185), so a thin ILIKE result would get padded with
  nearest-neighbor facts that then all get deactivated;
- `delete_memory` ("удали из памяти X") — an exactly-one-match result is
  archived *immediately* without confirmation; a lone semantic neighbor
  on an empty ILIKE would archive the wrong fact.

Destructive operations use exact matching only; semantic recall is a
read-path feature.

**Backfill is a separate, explicit script**
(`scripts/migrate_memory_embeddings.py`), dry-run by default, `--apply`
only on explicit human go-ahead — same caution as
`scripts/migrate_arcana_legacy.py`, even though the target is Postgres,
not Notion: it's still a production write.

**Location/tz stays out of RAG.** `resolve_offset` needs to return a
*verified* integer UTC offset — semantic recall gives you "closest
matching text," not a value safe to trust for scheduling. Folding tz
resolution into memory-RAG would trade a deterministic lookup for a
probabilistic one on a value where being silently wrong breaks every
scheduled reminder. `CITY_TZ` stays a hardcoded dict (patched with the
one missing town from the triggering bug); the existing Haiku fallback in
`_update_user_tz` remains the "unknown city" path. The two originally
reported memory-write bugs (duplicate `save_memory()` call in
`timezone_update`; category values outside `core/memory.py:CATEGORIES`)
were fixed as small, separate commits in the same change — real
correctness fixes, not RAG work, that happened to surface from the same
bug report.

## Alternatives considered

- **Mirror table (`memory_embeddings`)** — rejected; see Schema above.
- **Always-on semantic search** — rejected; adds a Voyage network round-trip
  to every search/context-injection call, most of which ILIKE already
  answers correctly and near-instantly, and (without billing on the Voyage
  account) burns the shared 3 RPM free-tier budget on a single message —
  see Decision above for the full reasoning.
- **Per-handler indexing (mirroring Arcana's `_rag_index_safe` call
  sites)** — rejected; memory already has one write path shared by both
  bots, duplicating the wrapper call at every handler would be exactly the
  kind of Nexus/Arcana divergence the project's shared-primitives
  principle exists to avoid.
- **Fold city/tz lookup into memory-RAG** — rejected; see Decision,
  offset needs to be a verified int, not a probabilistic text match.

## Consequences

- Two RAG consumers (`arcana_triplets`, `memories`) share one Voyage
  account's rate limit and free 200M-token allowance. With a payment
  method on file the RPM ceiling is 2000/8M TPM (Tier 1) rather than the
  card-less 3 RPM, so rate contention between the two consumers is not the
  live risk it would be otherwise; the hybrid ILIKE-first strategy still
  holds on its own merits (latency, exact-match quality — see Decision).
- `core/repos/memories_table.py`'s SQLAlchemy Core `Table` intentionally
  does *not* know about the `embedding` column — all embedding
  reads/writes go through raw SQL in `core/memory_rag.py`, exactly
  mirroring how `core/rag.py` treats `arcana_triplets`. This keeps
  `pg_memory_repo.py`'s existing CRUD untouched but means the two files
  must be kept in sync by convention, not by a shared ORM type.
- Existing rows have no embedding until the backfill script runs — search
  quality for old facts is unchanged (ILIKE-only) until then; new facts
  are indexed live from this change onward.
- The semantic query supports a `min_score` cutoff (`search_memory_semantic`),
  but no caller passes one yet — the query still logs the raw score
  distribution on every call instead of filtering, so a real cutoff can be
  picked from logged production data rather than guessed (#185 stays open
  for the actual calibration + wiring a default through from
  `core/memory.py`). Until then, nothing-relevant queries still return
  top-k nearest neighbors as-is.
- (#186, closed) Indexing originally hooked only `save_memory()`, so writes
  that go straight through the repo (auto-suggest confirm `save_parsed`,
  budget `_save_memory_entry` upserts, location `set_user_location`) had no
  embedding, and an upsert of a previously-embedded row kept a stale vector
  for the old fact text. Fixed by moving the hook into
  `PgMemoryRepo.add`/`.upsert` (see Decision) — every writer now gets fresh
  embeddings, and `upsert`'s update path nulls the stale vector before
  reindexing.
- (#187, closed) `arcana/handlers/rituals.py` fetched
  `get_memories_for_context` and discarded the result — dead even before
  this change, but after it the dead call could burn up to 3 semantic
  Voyage requests per ritual save for a result nobody read. Removed; a
  real ritual-context feature can re-add it deliberately.
