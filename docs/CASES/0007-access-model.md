# ADR-0007 — Access model: single owner, deferred guests, calendar visibility tiers

- **Status:** Accepted
- **Date:** 2026-06-13
- **Relates to:** ADR-0002 (core/domain split, identity in core)

## Context

The system needs an access/visibility model. The prior implementation carried a coarse role enum (test/friend/owner), per-domain boolean access flags, and per-user data relations. Reviewing actual usage: the bots (Nexus, Arcana) have exactly one user — the owner — operating from two Telegram accounts (personal and work). The per-user data separation, originally added to support a second account, proved unwanted: the owner wants one shared dataset regardless of which account is active. The roles/flags were built for needs that did not materialize as in-app access (a manager-as-user; a separate second account). The one genuine present sharing need is calendar visibility for friends plus a public booking surface.

## Decision

- **Single owner.** In-app data (tasks, finance, clients, notes) belongs to one owner. Identity lives in `core` (per ADR-0002); one owner may link multiple Telegram IDs (personal + work) to a single shared dataset. No per-account data partitioning.
- **No in-app RBAC for now.** The coarse role enum and per-domain access flags are not ported as a general permission system; they collapse to "owner." This is a YAGNI decision: there is exactly one in-app user.
- **Guests are deferred and per-domain.** Real future guests — a narrow Arcana manager (a couple of functions), a cat-care sitter (cats domain only) — are designed as narrow, domain-scoped access when they actually exist, not as speculative general machinery now.
- **Calendar visibility tiers.** The one present sharing need is the calendar (a `core` entity shared across Nexus and Arcana). Three owner-controlled visibility levels:
  - **owner** — sees everything;
  - **friends (via link)** — see event details and get booking priority; per event, the owner chooses to expose details or only a "busy" placeholder;
  - **public (site)** — free/busy only, plus a booking landing with anti-spam protection.

  The "friend" concept therefore lives at the calendar-sharing layer, not as an in-app role.

## Alternatives considered

- **Port the role enum + per-domain flags as an RBAC system** — rejected: builds a permission system for a single-user product; the flags were all-or-nothing per domain and could not express the real needs (per-event calendar visibility, narrow guest scopes).
- **Multi-tenant (each user isolated)** — rejected: there is one owner; the second-account use case dissolved into "link both accounts to one identity."

## Consequences

- Minimal, right-sized: no permission system to build or reason about now.
- Identity in `core` cleanly supports multiple Telegram IDs mapping to one owner with shared data.
- Guest access (Arcana manager, cats sitter) stays an additive, per-domain design introduced when each first real guest appears.
- Calendar sharing is handled at the calendar layer (visibility tiers + booking), decoupled from in-app access; detailed separately in the calendar architecture work.
- Migration note: data from both Telegram IDs must be merged under the single owner.
