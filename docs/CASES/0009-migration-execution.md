# ADR-0009 — Migration execution: repository seam, seal-then-swap, dual-write

- **Status:** Accepted
- **Date:** 2026-06-13
- **Relates to:** ADR-0002 (core/domain split), ADR-0003 (ports & adapters)

## Context

The migration from Notion to Postgres must move not just data but a working CRM's behavior (especially Arcana) without downtime or rewrites. A diagnostic of the Arcana code found that data access is centralized in one module (`core/notion_client.py`, ~60 functions) rather than scattered — a real seam — but the seam is leaky: Notion-shaped structures (page IDs as strings, rich-text property dicts) leak into handlers, which assemble Notion props and call generic update helpers directly. So the persistence layer cannot simply be swapped; in places handlers assume Notion's data shape.

## Decision

Migrate behavior behind a **repository seam**, per domain, in two stages, never rewriting business logic:

1. **Seal the seam (refactor, no behavior change).** Introduce a repository per domain (e.g. `WorksRepo`, `ClientRepo`, `SessionRepo`) that returns plain domain objects (dataclasses) and exposes domain-named methods. All Notion-shaped data (page IDs, rich-text props) is confined inside the repository; handlers speak only domain terms. The repository internally calls the existing Notion data-access functions (the Notion adapter). Tests stay green; behavior is identical 1:1.
2. **Swap the backend (behind the now-clean repository).** Replace the Notion adapter with a Postgres adapter via **dual-write**: write to Notion and Postgres in parallel, verify, switch reads when verified, with rollback always available.

**Sequencing:** start with the cleanest, smallest domain to validate the pattern cheaply; do the largest/leakiest domain (`sessions.py`, ~1500 lines) last, once the pattern is proven.

## Alternatives considered

- **Big-bang rewrite** — rejected: high risk, downtime, and discards working, tested behavior.
- **Swap `notion_client.py` wholesale for a Postgres version** — rejected: the seam is leaky; handlers assume Notion shapes in places, so a direct swap would break them. Sealing first is required.

## Consequences

- Business logic (the CRM behavior) is preserved, not rewritten; risk is contained per domain.
- The repository acts as an anti-corruption layer: domain logic stops depending on Notion's data model.
- Dual-write gives a reversible cutover with verification.
- Cost: an upfront per-domain refactor to seal the seam, heaviest where Notion shapes leak most.
- Validated: the `works` domain pilot introduced `WorksRepo` with a `Work` domain object, refactored its handlers, and kept all tests green — confirming the pattern before tackling harder domains.
