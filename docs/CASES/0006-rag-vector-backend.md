# ADR-0006 — RAG: vector backend (Qdrant → pgvector)

- **Status:** Accepted
- **Date:** 2026-06-13 (proposed) → 2026-06-25 (accepted)
- **Relates to:** ADR-0001 (memory layering), ADR-0002 (core/domain split),
  ADR-0003 (ports & adapters), ADR-0005 (observations as RAG source)
- **Code conforms to:** `core/rag.py`, Alembic migration for `arcana_triplets` (pgvector)
- Update this ADR in the same PR that changes the vector backend or schema.

## Context

Arcana indexes reading triplets (cards · question · interpretation) and recalls
semantically similar past readings — to keep the author's voice consistent and to
surface a client's own history into the interpretation prompt.

The vectors originally lived in a Qdrant container that belonged to a *neighbouring*
bot (`klgpff`), reached over an external Docker network. That coupling was the problem,
not a design: when `klgpff` was down, the external network was absent and RAG silently
became a no-op — Arcana's recall depended on an unrelated project's container
lifecycle. Qdrant had been picked there for that bot's own reasons (throughput at
scale), and Arcana piggy-backed on it for convenience, not fit.

## Decision

Decouple. Store vectors in **pgvector**, in the **same Postgres** Arcana already runs
(`core.db.get_engine()`), table `arcana_triplets` (created by an Alembic migration, not
at runtime). No separate service, no external network, no cross-project dependency.

- **Embeddings: Voyage `voyage-4-lite`, dim 1024.** Deliberately different from
  `klgpff` (`voyage-3-lite`/512) so the two draw on separate free Voyage token pools
  and never collide.
- **Rate-limit aware.** Voyage's free tier is 3 RPM. The client runs `max_retries=5`
  (native wait-and-retry), and `index_triplets_batch` embeds N triplets in a single
  request — one reading's worth of triplets costs one Voyage call, not N.
- **Cosine search via pgvector:** `embedding <=> CAST(:q AS vector)`, HNSW
  `vector_cosine_ops`, score = `1 - distance`. Vectors are passed as text literals
  `[v1,v2,…]` + `CAST(:q AS vector)` — no `pgvector` Python package / register_vector
  needed.
- **Graceful by contract.** Missing `VOYAGE_API_KEY`, an unreachable DB, or absent
  pgvector → warning + empty/no-op, never an exception. A reading still works without
  RAG.
- **Scope: live reading triplets only**, upserted by `session_id` (re-indexing an
  edited reading is idempotent); `client_id` set → recall within one client's history,
  unset → recall across all readings (author-voice consistency).

## Alternatives considered

- **Stay on Qdrant** — rejected. It was never chosen *for Arcana*: it was the
  neighbour's container Arcana borrowed for speed, which made recall hostage to an
  unrelated bot's uptime. Qdrant's real zone is millions of vectors with distributed
  filtering — a scale a personal, hand-/voice-driven practice will never reach. Running
  it for Arcana means a separate service + RAM on a 2 GB VPS for headroom that never
  materializes.
- **Keyword / no-vector RAG** — rejected for this path. Layered prompt assembly (deck
  reference + memory + recent readings injected as sections) already covers the
  deterministic-context case (see PORTFOLIO §5.1); it does *not* do recall *by meaning*
  across history, which is the whole point here.

## Consequences

- Right-sized: zero added services, no external Docker network, fits the existing
  Postgres/VPS. Recall no longer depends on `klgpff`.
- Embeddings can be filtered/joined with domain rows in a single SQL query.
- **Trade-off — revisit at scale:** pgvector + HNSW is sufficient for a personal
  practice (thousands of vectors). At millions of vectors / distributed-filtering needs
  the choice would be re-opened (Qdrant back on the table) — a scale this practice will
  not hit.
- Docs realigned in the same PR: `docs/ARCHITECTURE.md` (semantic recall moved from
  "What's next" to a shipped decision) and `docs/PORTFOLIO_AI_FEATURES.md` (§5.4 added,
  §5.1 clarified as deterministic, vector-free).
