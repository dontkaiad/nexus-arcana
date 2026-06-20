# ADR-0007 — Identity Domain: Migrate 🪪 Пользователи from Notion to PostgreSQL

**Date:** 2026-06-16  
**Status:** Accepted  
**Domain:** `core.identity` (user resolution)

## Context

All bot handlers and miniapp routes need three things per request:
1. `notion_page_id` — to write Relation fields in Notion (tasks, sessions, works…)
2. `permissions` — to gate features (nexus / arcana / finance)
3. `name`, `role` — for display and access logic

Previously `core/user_manager.py` fetched this from Notion `🪪 Пользователи` DB on every
uncached request (5-min in-process TTL). This adds ~250ms Notion API latency on cache miss
and ties permission checks to Notion availability.

N=2 rows at migration time (single owner, two devices).

## Decision

Introduce `core_identity` PostgreSQL table as the authoritative source for user identity.
`core/user_manager.py` public API (`get_user`, `check_permission`, `get_user_notion_id`)
is unchanged — callers see zero diff. The backend switches from Notion to PG.

### Table schema

```
core_identity (
  notion_id   TEXT PRIMARY KEY,   -- Notion page ID; matches user_notion_id in all other tables
  tg_id       BIGINT NOT NULL,    -- Telegram user ID (unique index)
  name        TEXT,
  role        TEXT,               -- 'Владелец' | 'Друг' | 'Тест'
  perm_nexus  BOOLEAN,
  perm_arcana BOOLEAN,
  perm_finance BOOLEAN,
  created_at  TIMESTAMPTZ
)
```

`notion_id` is the primary key because all existing PG tables store `user_notion_id TEXT`
as an owner-key referencing this value. No FK constraint — the owner-key pattern is sufficient
for a single-owner system and avoids migration complexity on existing populated tables.

### Owner-key pattern

All PG tables (`nexus_tasks`, `nexus_notes`, `nexus_budget`, `nexus_lists`, `arcana_pnl`,
`arcana_inventory`, `core_memories`) already have `user_notion_id TEXT NOT NULL`. This column
is the owner-key: `core_identity.notion_id == other_table.user_notion_id`. No FK constraints
added — cross-joins are done in application code, not at DB level.

### `get_owner_notion_ids()` cutover

`notion_client.get_owner_notion_ids()` previously queried Notion for all pages with
`Роль='Владелец'`. Now reads from `core_identity` PG, filtering by `role='Владелец'`.
Same 10-min in-process cache retained.

## Alternatives Considered

**Keep Notion as source of truth** — rejected. Every user-gated request adds 250ms Notion API
round-trip on cache miss; Notion downtime blocks all permission checks.

**Add FK constraints in existing tables** — rejected. Requires back-filling all tables before
adding constraint; adds coupling between identity domain and every other domain. Owner-key is
sufficient for single-owner use case.

**Store tz_offset in core_identity** — deferred. Timezone is currently stored in `core_memories`
under key `tz_{tg_id}` and read via `_get_user_tz()`. Moving it would require migrating the
memory read path. Out of scope for this ADR.

## Consequences

- `get_user(tg_id)` now hits PG (sub-millisecond) instead of Notion (~250ms) on cache miss.
- Permission checks are available even during Notion downtime.
- `notion_id` primary key = natural join to all other PG tables without FK overhead.
- `backfill_identity.py` is idempotent (upsert on conflict); re-run is safe after Notion changes.
- Notion `🪪 Пользователи` DB still used for relation backlinks from tasks/sessions/works —
  those are Notion-side relations and do not go through `user_manager`.
