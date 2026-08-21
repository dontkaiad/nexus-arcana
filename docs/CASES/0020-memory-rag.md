# ADR-0020 — RAG search over 🧠 Память (shared memory, both bots)

- **Status:** Accepted
- **Date:** 2026-08-21
- **Relates to:** ADR-0005 (memory store), ADR-0006 (RAG vector backend —
  first pgvector consumer), ADR-0016 (single-writer location)
- **Code conforms to:** `core/memory_rag.py`, `core/memory.py`, Alembic
  migration `y5z6a7b8c9d0_memories_embedding_pgvector.py`,
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

**Indexing hook: inside `core/memory.py:save_memory()`, not per-handler.**
`save_memory` is already the single write path both bots funnel through
(scope field distinguishes bot). A thin wrapper
(`core.memory._rag_index_memory_safe`, calling `core.memory_rag.index_memory`)
runs right after a successful write, embedding `fact_text + related_to +
category` (category/related_to add real retrieval signal; the machine-slug
`key_name` is excluded as noise). It fires *after* the user-facing
`message.answer(...)`, so Voyage's network round-trip never delays the
visible "🧠 Запомнил" ack. This differs from Arcana's own
`_rag_index_safe()` wrapper pattern (called explicitly at each
sessions.py call site) — that pattern only exists there because sessions
are Arcana-only; memory is already the shared choke point, so both bots
get indexing automatically without their handlers needing to call anything.

**Search: hybrid, ILIKE first, semantic only as a thin fallback.** Every
search path (`search_memory`, `get_memories_for_context` via
`_find_pages_by_hint`) runs the existing ILIKE query first; a semantic
query only fires when ILIKE returns fewer than 3 hits, merged in
(ILIKE-first, deduped by id, capped at the existing page size). Always-on
semantic search was rejected: `get_memories_for_context` can fire several
times per single incoming Telegram message (once per extracted keyword),
and Voyage's free tier is 3 RPM — always-on would burn the shared budget
(now split across two RAG consumers) on a single message. The recursive
alias resolver (`_resolve_alias` → `_find_pages_by_hint`) opts out of the
semantic fallback entirely (`use_semantic=False`) — alias recall needs to
stay exact; a "close enough" semantic match at each recursion level risks
silently renaming the wrong entity.

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
- **Always-on semantic search** — rejected; burns the shared 3 RPM Voyage
  budget on every search/context-injection call, most of which ILIKE
  already answers correctly and near-instantly.
- **Per-handler indexing (mirroring Arcana's `_rag_index_safe` call
  sites)** — rejected; memory already has one write path shared by both
  bots, duplicating the wrapper call at every handler would be exactly the
  kind of Nexus/Arcana divergence the project's shared-primitives
  principle exists to avoid.
- **Fold city/tz lookup into memory-RAG** — rejected; see Decision,
  offset needs to be a verified int, not a probabilistic text match.

## Consequences

- Two RAG consumers (`arcana_triplets`, `memories`) now share one Voyage
  free-tier budget (3 RPM) — worth monitoring if either surface's usage
  grows; the hybrid ILIKE-first strategy is the main guardrail against
  starving one consumer of budget the other needs.
- `core/repos/memories_table.py`'s SQLAlchemy Core `Table` intentionally
  does *not* know about the `embedding` column — all embedding
  reads/writes go through raw SQL in `core/memory_rag.py`, exactly
  mirroring how `core/rag.py` treats `arcana_triplets`. This keeps
  `pg_memory_repo.py`'s existing CRUD untouched but means the two files
  must be kept in sync by convention, not by a shared ORM type.
- Existing rows have no embedding until the backfill script runs — search
  quality for old facts is unchanged (ILIKE-only) until then; new facts
  are indexed live from this change onward.
