# ADR-0008 — Lookup tables: per-concept reference tables, code-keyed

- **Status:** Accepted
- **Date:** 2026-06-13
- **Relates to:** ADR-0002 (core/domain split), ADR-0005 (parked finance categories)

## Context

In the Notion implementation, the same enumerated values were duplicated as per-database `select` options across many databases, and they drifted: e.g. "Cash" appeared with different emoji in different databases (💵 vs 💸), and priority markers and client-type labels diverged. The decorated string (emoji baked into the value) was the source of truth, so every duplicate could drift independently. Migrating to Postgres needs a model for these repeated values that prevents drift by construction.

## Decision

- **Per-concept lookup tables.** Each enumerated concept that is shared across tables — or that carries display metadata — gets its own small reference table (e.g. `payment_source`, `priority`, `client_type`). Domain tables reference it by foreign key. The value lives in exactly one place.
- **Code-keyed, display as attributes.** A lookup row is `{ code (stable key), label, emoji, color, sort_order, is_active }`. Domain rows reference the stable `code`, never the decorated string. Changing an emoji or label is a one-row edit that propagates everywhere; drift is structurally impossible.
- **Don't over-normalize.** Selects that are purely local (used in one place, no metadata) stay as a plain enum / CHECK constraint rather than a table, to avoid table sprawl.

## Alternatives considered

- **A) Per-field selects (status quo)** — rejected: this is exactly what produced the drift; no single source of truth.
- **C) One universal lookup table (OTLT / MUCK)** — rejected: a single table for all enumerated values cannot enforce referential integrity per concept (any column could reference any value), loses type safety, and couples unrelated domains. A recognized anti-pattern.

## Consequences

- The emoji/spelling-drift class of bug is eliminated by construction: one value, one row, one emoji.
- Foreign keys provide referential integrity: invalid values become impossible.
- Display metadata (emoji, color, order) is centralized and editable in one place.
- Migration follow-up: inventory the databases, collapse drifted variants (e.g. "Наличные / 💵 / 💸") into a single `code='cash'`, and remap existing rows to codes.
- Relates to ADR-0005's parked finance categories: those are enumerated values too and resolve as finance-module lookups vs config during the same pass.
