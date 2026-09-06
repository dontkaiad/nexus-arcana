# ADR-0024 — `core_identity.notion_id` keeps its name after the `user_notion_id → user_id` rename

**Date:** 2026-09-07
**Status:** Accepted
**Domain:** `core.identity` + every owner-scoped table

## Context

#144 renamed the owner-key column `user_notion_id` → `user_id` across all 14
PG tables (`memories`, `tasks`, `sessions`, `nexus_lists`, …) and the whole
Python surface (`get_user_notion_id` → `get_user_id`, params, kwargs, raw
SQL). Notion is gone; the column never held anything but the internal user
id, so the `notion` in the name was misleading.

That leaves `core_identity.notion_id` — the PRIMARY KEY the owner columns
point at (logical FK, not enforced). Renaming it too would be consistent but
is a different risk class: it is a live PK, drives `ON CONFLICT
(notion_id)` upserts in `pg_identity_repo`, and is exposed to the miniapp as
`notion_page_id`. #149 already decided to leave it (it is the real owner
key, not a migration artifact).

## Decision

Leave `core_identity.notion_id` as-is. The 14 FK-style columns become
`user_id`; the PK they reference stays `notion_id`.

## Consequences

- Mild inconsistency: `tasks.user_id` logically references
  `core_identity.notion_id`. A reader has to know they are the same value
  space (a Notion-page-id-shaped string that is now just "the user id").
- No PK migration, no upsert-conflict-target change, no miniapp contract
  change (`notion_page_id` JSON key untouched — that is Phase C, optional).
- If the inconsistency ever grates, renaming the PK is a self-contained
  follow-up: one `ALTER TABLE core_identity RENAME COLUMN`, the
  `index_elements` in `pg_identity_repo._upsert_sync`, and `IdentityUser`.

## Alternatives considered

- **Rename the PK too, same PR.** Rejected: widens a mechanical rename into
  an identity-layer change; the upsert conflict target and the miniapp
  `notion_page_id` key would move in the same commit as 216 mechanical file
  edits, making review and rollback harder for no functional gain.
