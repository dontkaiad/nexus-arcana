# ADR-0004 — Error logging: stdout/journald with a Telegram alert sink

- **Status:** Accepted
- **Date:** 2026-06-13
- **Relates to:** migration off Notion-as-database

## Context

Runtime errors from both bots (Nexus, Arcana) were written to a Notion database (message, traceback, error type, logger name, bot, user). This couples the logging path to a remote service over the network: every error triggers a Notion API call, and the schema even carried a `notion_error` type — so when Notion is unavailable, errors about Notion cannot be recorded. A logging path must not depend on a fallible external service.

The error data is machine-written telemetry, not human-curated content, so a database UI adds little value. Separately, the operator (mobile-first) needs to notice failures without polling a server.

## Decision

- **Drop the Notion error database.** Errors are written to **stdout**, captured by **journald** on the VPS — the source of truth (12-factor logs: the app does not manage log routing; the platform collects).
- **One dedicated Telegram alert bot** is the notification sink for the whole server, with its own token, separate from the Nexus and Arcana bots:
  - **Application errors** (Nexus, Arcana) are forwarded in-process via a custom `logging.Handler` at `ERROR` and above.
  - **System events** (service crash, disk/RAM thresholds) are emitted by `systemd OnFailure=` hooks and a small timer — the application cannot observe these itself.
- Alerts are **severity-gated** (ERROR+) and **deduplicated / rate-limited** to avoid alert fatigue.
- The alert channel is **best-effort**: if Telegram delivery fails, the error still lives in journald. journald is the source of truth; Telegram is a convenience surface.

## Alternatives considered

- **Keep the Notion error database** — rejected: couples logging to a fallible remote service (illustrated by the `notion_error` type), costs an API call per error, and provides a UI that machine telemetry doesn't need.
- **Hosted observability (e.g. Sentry) now** — rejected for now: overkill and an extra dependency/cost for a single 2 GB hobby VPS. Deferred; it plugs into the same `logging` interface later if needed.
- **Send alerts from the main bot instead of a separate bot** — rejected: a separate sink keeps alerts out of user-facing UX and, critically, survives a crash of the bot it observes (a down bot can still be reported).

## Consequences

- **Free, standard, decoupled:** no per-error network call; logging no longer depends on any external service being up.
- **Mobile-native awareness:** push alerts to Telegram instead of polling a server.
- **Resilient alerting:** the separate alert bot can report failures of the observed bots.
- **Trade-offs:** no queryable error-history UI out of the box (journald retains and greps; a Postgres `error_log` table + Mini App view is a documented future option). Alert quality depends on disciplined severity gating and deduplication.
- **Security:** no credentials involved; the alert bot uses its own token.
