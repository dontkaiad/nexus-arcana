# ADR-0001: Memory architecture — structural layer + vector retrieval layer

## Status
Accepted (planning phase; implementation during Notion → PostgreSQL migration)

## Context
The bots store two fundamentally different types of "memory":

1. **Exact key-based facts** — user timezone, category limits, income, goals, preferences, personalization profile (fed to system prompts). Retrieved deterministically: by key/category, with filters and aggregates.
2. **Unstructured corpora** — grimoire (spells/recipes), past tarot interpretations, client request/note history. Retrieved by meaning ("find similar"), not by exact key.

Today everything lives in Notion as key-value/rows. Migration to PostgreSQL raised a question: should we introduce RAG (vector search via embeddings), and if so, should it replace the current memory entirely?

## Decision
Memory is not replaced by RAG. Two-layer architecture:

- **Structural layer** (PostgreSQL, relational tables) — for exact-by-key facts. Deterministic, with filters and aggregates. Source of record.
- **Vector layer** (RAG) — on top of unstructured corpora, for similar-by-meaning. This is an index/supplemental retrieval, not source of truth.

Both layers feed the prompt together: structural facts as hard context, RAG snippets as soft context.

## Alternatives considered
1. **Everything in vectors (RAG as universal memory).** Rejected: exact values (limits, tz, sums) are unreliably retrieved via similarity search; aggregates and filters ("all active limits", "sum by category") are lost; hallucination surface grows. Vector search answers "similar", but config facts require "exact".
2. **Everything in SQL, no vectors.** Rejected: semantic search over grimoire/history via `LIKE`/full-text operates on words, not meaning — doesn't scale to paraphrases and synonymous queries.

## Consequences
- (+) Each query routes to the tool matching its retrieval type → exact-fact reliability preserved; semantics added only where needed.
- (+) Embedding boundary is explicit: need exact-by-key → SQL; need similar-by-meaning → vector.
- (−) Two retrieval paths = more code and infrastructure than one.
- Boundary categories (freeform observations: insights, patterns) can live in both layers: source of record in SQL, embedding as index on top. Duplication is intentional.

## Open question (future ADR)
Vector backend: dedicated Qdrant vs `pgvector` inside PostgreSQL. On small corpus and 2 GB RAM, `pgvector` is simpler (one service, transactions with relational data, single backup); Qdrant scales better and supports filterable payloads. Decided in a future ADR with real corpus measurements.
