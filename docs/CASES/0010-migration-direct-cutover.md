# ADR-0010: Migration strategy — direct cutover (supersedes ADR-0009)

## Status
Accepted. Supersedes ADR-0009 (dual-write).

## Context
We are migrating both bots' persistence from Notion to PostgreSQL, one domain at a time
(vertical slices). ADR-0009 specified a phased **dual-write**: write to Notion and Postgres
in parallel, reconcile, then cut over — the standard way to migrate a *live* system without
downtime.

Starting the first slice (rituals), two facts invalidated that premise:
- **No live traffic during migration.** The bots are not in production use until the move
  completes, so no new records are created concurrently.
- **Empty source tables.** The Notion source holds only test data; there is nothing to
  backfill or reconcile.

Dual-write exists to protect a live system from downtime and data loss during the switch.
Neither condition holds here.

## Decision
Migrate each domain by **direct cutover**:
designed schema → Alembic migration → repository adapter pointed at Postgres → verify →
decommission the Notion backend for that domain.

This removes the `notion_id` linking column, the parallel-write phase, and the
backfill/reconciliation step.

## Alternatives considered
- **Dual-write (ADR-0009).** Rejected: its complexity only pays off when zero-downtime is
  required. With no live traffic and empty sources it adds parallel-write code, a
  reconciliation step, and a `notion_id` column that serve no purpose here.
- **Dual-write kept purely to rehearse the pattern.** Rejected: justified only as a learning
  exercise, not an engineering need. Carrying machinery the situation doesn't call for is the
  opposite of the signal we want.

## Consequences
- Simpler, faster per-domain migration; less code to add now and remove later.
- The cutover is **not** zero-downtime — acceptable, because there is no live traffic to
  interrupt.
- Scoped to the current pre-launch state. If the system were already serving users,
  dual-write would be correct; this ADR records why it is not, here.
