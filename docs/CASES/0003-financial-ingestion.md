# ADR-0003 — Financial data ingestion: statement import via ports & adapters

- **Status:** Accepted
- **Date:** 2026-06-13
- **Supersedes / relates to:** ADR-0002 (domain coupling)

## Context

The system tracks personal finances (Nexus domain — a personal budget ledger) and practice finances (Arcana domain — revenue recorded on client records, with a computed P&L), kept separate per ADR-0002. The budget engine computes a per-category daily spend allowance plus streaks; it must read a complete, trustworthy transaction ledger.

The open question was **how real-world bank transactions enter that ledger**, under hard constraints: free, works on iOS (iPhone), no credentials handed to any third party, and complete (no silent gaps).

Environment constraint (Russian retail banking): consumer bank APIs exposing an individual's own transaction history do not exist — programmatic bank access is gated to business accounts (sole proprietor / legal entity with a settlement account). There is no PSD2-style consumer open-banking aggregation.

## Decision

Ingest financial data through a **source-agnostic port** (ports & adapters / hexagonal): the budget engine depends on an `ingest(transactions)` port, never on a specific source.

- **Primary adapter — bank statement import (`statement_import`):** the user exports an account statement (web/app export — no app-store app or push required, so iOS-compatible). An AI parser (Vision/OCR) normalizes it and writes to the ledger.
- **Secondary adapter — `manual_entry`:** cash and optional same-day large spends.

Each ledger row carries dimensions: `source` (`manual` | `imported`), a `reconciled` status, and an `account` reference (supports multiple future accounts).

The **budget engine is unchanged** — it reads the ledger and does not require real-time data.

Statement delivery is account-type dependent: a **personal account** requires a manual daily export (supported by an in-app daily reminder); fully unattended daily delivery (scheduled email) exists **only on a business account** and is out of scope for now.

## Alternatives considered

- **Personal bank API auto-pull** — rejected: does not exist for individuals in this market (business-tier only).
- **Real-time per-transaction adapters** (per-transaction email, mobile push capture, notification scraping) — rejected: channels either don't exist, are paid subscriptions, or are sandboxed on iOS; each broke the consistency requirement and was platform-bound.
- **Receipt photo as primary source** — rejected as primary: incomplete (misses cash-by-card and no-receipt purchases); retained only as a supplementary input.
- **Real-time as a hard requirement for the budget engine** — rejected: the daily allowance only needs a ledger complete as of "today"; a once-daily statement closes the day cleanly.

## Consequences

- **Trade-off — not zero-touch on a personal account:** one ~10-second export per day, mitigated by an in-app reminder; statement completeness forgives any skipped manual entries.
- **Bank-agnostic, iOS-compatible, free, complete** — the statement captures every card charge regardless of merchant or receipt.
- **Extensible:** a future business-account scheduled-email adapter (or any other source) plugs into the same port without touching the budget engine.
- **Per-bank parsing cost:** statement formats (pdf/xlsx) differ per bank; each requires a parsing template.
- **Security:** no credentials are ever handled by the assistant; ingestion uses only user-exported files or tokens the user issues themselves.
