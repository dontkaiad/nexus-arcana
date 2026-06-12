# ADR-0002: Nexus & Arcana coupling — modular monolith with a shared kernel

## Status
Accepted

## Context
Nexus (personal-assistant bot) and Arcana (tarot-practice CRM bot) are run by a
single user but serve two distinct domains. Today they share several Notion
databases (finance, memory, lists) distinguished by a `bot` column. Migrating
from Notion to PostgreSQL forces a decision on how the two apps relate at the
data and code layer.

Constraints:
- Some concerns are genuinely shared: the user's identity, timezone, memory, calendar.
- Some must stay domain-separate: finances — Arcana's practice P&L vs Nexus's personal budget.
- A single-pane view is required (Arcana work items surfacing inside Nexus).
- Cost must stay minimal: one VPS, one database.

## Decision
Adopt a **modular monolith** with a **shared kernel** + **per-domain modules**,
all in one PostgreSQL instance:
- `core` (shared kernel): identity/user, timezone, memory, calendar, access control.
- `nexus` domain: personal tasks, personal budget, lists.
- `arcana` domain: clients, spreads, rituals, grimoire, practice P&L.
- Cross-domain read views provide the single pane (e.g. a unified agenda combining
  Nexus tasks and Arcana work items).

## Alternatives considered
- **Two fully separate databases/apps.** Rejected: duplicates shared
  identity/timezone/memory/calendar and forces sync; breaks the single-pane
  requirement; doubles infrastructure cost.
- **One database, flat bot-scoping** (a `bot` column on shared tables — the
  current Notion model). Rejected: collapses genuinely separate domains into
  shared tables, making domain-specific rules (separate finances) a hack rather
  than a structural property; later extraction of a domain requires untangling.

## Consequences
- (+) Domain rules (separate finances) are enforced structurally, not by convention.
- (+) One database, one VPS — same running cost as flat bot-scoping; separation is
  logical (schemas/modules), not physical.
- (+) Extensible: new clients (native iOS app, web calendar, Apple Intelligence)
  consume one `core` API uniformly.
- (+) Optionality: the isolated `arcana` domain makes a future product extraction
  additive (multi-tenancy, billing, auth) rather than a rewrite. A free
  side-benefit — **not** a driver of this decision.
- (−) Requires defining and maintaining the core/domain boundary up front.
- (−) Cross-domain queries go through views, not one flat table.
