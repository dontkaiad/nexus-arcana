# ADR-0005 — Memory store: facts vs observations (and what is not memory)

- **Status:** Accepted
- **Date:** 2026-06-13
- **Relates to:** ADR-0001 (memory layering), ADR-0002 (core/domain split)

## Context

The current memory store (a single Notion database) conflates two different data shapes, plus content that is not really personal memory at all. The live schema holds an exact key->value pair (e.g. timezone, budget limits), a free-text natural-language field with a 15-value category taxonomy (preferences, insights, patterns, people, ADHD, etc.), provenance (auto-extracted vs manual), a currency flag, and a bot tag.

Two distinct access patterns are mixed: exact lookup by key vs fuzzy/semantic retrieval. Some entries are domain knowledge (e.g. cat-care notes) misfiled as personal memory.

## Decision

Split core memory into two tables:

- **`facts`** — exact key->value assertions about the user/world (timezone, limits). Retrieved deterministically by key. One authoritative current value per key.
- **`observations`** — free-text, categorized notes about the user and people (preferences, patterns, insights, ADHD adaptations). Retrieved by category and (later) semantic similarity via embeddings; the natural source for the RAG layer (ADR-0001).

Both live in `core` (shared kernel, per ADR-0002). Shared columns: `source` (auto | manual), `is_current` (soft currency — never hard-delete, keep history), `scope` (global | nexus | arcana — replacing the old bot tag; most facts are global).

**Crucially, domain knowledge bases are not memory.** Unstructured domain content (cat-care knowledge, the Arcana grimoire) lives in its own domain tables, and the RAG layer attaches to those — not to personal memory. Memory is about the user and people; domain knowledge is about a domain.

## Alternatives considered

- **One unified memory table** (nullable key/value/text) — rejected: mixes exact-lookup and semantic access patterns in one place, and invites domain knowledge to keep accumulating as misfiled "memory."
- **Replace memory with RAG entirely** — rejected in ADR-0001: exact facts need deterministic key lookup, not similarity search.

## Consequences

- Clean separation of deterministic facts from fuzzy observations; the `observations` table is the obvious embedding/RAG source.
- Migrating off the single store includes re-homing misfiled content: cat-care notes move out of memory into the (future) `cats` domain knowledge base.
- **Parked follow-up:** several current "memory" categories (income, limits, debts, goals) are financial configuration; they likely belong to the finance module (ADR-0003), not core memory — decided separately.
- `scope` keeps memory in `core` while allowing the rare domain-specific fact, without splitting memory per domain.
