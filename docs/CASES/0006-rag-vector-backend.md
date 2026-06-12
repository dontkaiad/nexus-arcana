# ADR-0006 — RAG: vector backend and retrieval layer

- **Status:** Accepted
- **Date:** 2026-06-13
- **Relates to:** ADR-0001 (memory layering), ADR-0002 (core/domain split), ADR-0005 (observations as RAG source), ADR-0003 (ports & adapters)

## Context

The RAG layer retrieves semantically relevant context from domain knowledge bases (cat-care, the grimoire) and the `observations` table (ADR-0005), replacing the current naive approach of injecting a few arbitrary memory rows. Two choices were open: the vector backend, and where retrieval lives.

Earlier project notes named Qdrant as the vector store, but as an unjustified default — no comparison against alternatives — and without weighing the deployment constraint: a single 2 GB RAM VPS already running Postgres, multiple bots, a transcription worker, and an API backend.

## Decision

- **Vector backend: pgvector.** Vectors are stored in the existing Postgres (one store, per ADR-0002): no separate service, no extra RAM, and embeddings can be filtered/joined with domain rows in a single query. Sufficient for the system's scale (personal app, thousands of vectors) with HNSW indexes.
- **Retrieval lives in `core`** as a domain-agnostic service, pointed at its sources (domain KBs + `observations`), **behind a retrieval port** — consistent with the ports & adapters discipline (ADR-0003). The embedding provider sits behind its own port (API now; local embeddings a future option).
- **Qdrant is the documented upgrade path**, not the starting point: adopted only when scale, throughput, or distributed-filtering needs exceed what pgvector serves, at which point a Qdrant adapter is written behind the same retrieval port with no caller changes.

## Alternatives considered

- **Qdrant now (the prior default)** — rejected for now: a separate service costs RAM and operational overhead on a 2 GB VPS and only wins at a scale this system does not have. Adopting it now would mean upsizing the VPS purely to run it — overkill.
- **Replace exact-fact lookup with vector search** — out of scope: exact facts are retrieved deterministically by key (ADR-0005), not by similarity.

## Consequences

- Right-sized infrastructure: zero added services or cost; fits the 2 GB box.
- The retrieval port keeps a future pgvector→Qdrant migration a swap, not a rewrite.
- A single retrieval layer spans multiple sources (domain KBs + observations), filtered by source/domain/scope; exact facts stay on deterministic key lookup.
- The embedding-provider port keeps the choice of embedding model (API or local) independent of the rest of the system.
