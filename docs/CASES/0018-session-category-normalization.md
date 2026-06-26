# ADR-0018 — Session category as a first-class dimension

**Status:** Accepted  
**Date:** 2026-06-26  
**Relates to:** ADR-0008 (lookup tables — establishes the pattern used here),
ADR-0017 (scan-before-spell — same principle: add a deterministic signal rather
than repair a stochastic one)  
**Code conforms to:** 077d093 (drop spread_type; all 5 phases complete)  
**Verify against:** arcana/repos/sessions_tables.py, alembic/versions/w3x4y5z6a7b8_session_category.py,
alembic/versions/x4y5z6a7b8c9_drop_sessions_spread_type.py,
arcana/repos/pg_sessions_repo.py, arcana/handlers/sessions.py,
miniapp/backend/routes/arcana_sessions.py, core/schema.py, core/reply_update.py

---

## Context

`sessions.spread_type TEXT` is a single free-text column that carries three
orthogonal facts simultaneously:

1. **Shape** — card count format: Triplet (3 cards), Celtic Cross (10 cards).
2. **Category / practice** — what the session *is about* esoterically: Ancestral work,
   Magical influence, Diagnostic, General life sphere.
3. **Topic of the question** — relationship, finances, health, etc.
   (already stored separately in `sessions.area`; `spread_type` partially
   duplicated it via coercion in `SESSION_CATEGORY_MAP`).

Three independent code paths write `spread_type`, each non-deterministically:

| Path | Parser | What Haiku receives | Haiku output |
|------|--------|--------------------|-----------:|
| Solo triplet (format C) | `PARSE_SESSION_SYSTEM` | `spread_type` field | shape or category, mixed |
| Multi-session (formats A/B) | `PARSE_SESSION_SYSTEM` | `session_category` field | category string, nullable |
| Vision (photo) | `VISION_SYSTEM` | `spread_type` field | shape string ("Другой") |

The `all_triplets` fallback (`sessions.py`) uses card *count* to infer category:
if every triplet is exactly 3 cards and Haiku returned no category, the stored
value becomes `🔺 Триплет` — shape wins over category as the default.

An emoji divergence compound the problem: `SPREAD_MAP` / `SESSION_CATEGORY_MAP`
use `🔺🌐✝️` while `core/schema.py` uses `🌀🔮🗝️` for the same concepts.
The `core/schema.py` values never reach the database (reply-update for sessions
does not parse `spread_type`), but readers that consume both sources see
inconsistent emojis.

There is no post-creation mechanism to correct a wrong category.

---

## Decision

### 1. Drop shape from storage

Shape (Triplet / Celtic Cross) is not a meaningful dimension for search,
analytics, or display. It is an artifact of how cards were laid out, not what
the session was about. It is removed from the data model entirely; the card
string itself encodes count implicitly.

### 2. Introduce `session_category` lookup + `category_id` FK

Following ADR-0008, a `session_category` reference table replaces the decorated
text column. Five values (see table below). The `sessions` table gains a
`category_id SMALLINT FK NULLABLE` column.

| code | emoji | label |
|------|-------|-------|
| sphere | 🌐 | Сфера жизни |
| ancestral | 🌳 | Родовой узел |
| magical | ⚡ | Магические воздействия |
| diag_ritual | 🔍 | Диагностика перед ритуалом |
| diag_ability | ✨ | Диагностика способностей |

`NULLABLE` is intentional: Vision sessions without client history get
`category_id = NULL`; the practitioner corrects manually (see phase 4 — reply).

### 3. `area` stays per-triplet; session-level aggregation is API-computed

`sessions.area TEXT` already captures the question topic (Relationships, Work,
Finances, Health, etc.) per triplet. No new column is added; the mini-app API
layer aggregates unique area values across triplets when building a session group
response.

### 4. Client anchor — deterministic signal layered over Haiku stochasticity

Same design principle as scan-before-spell (ADR-0017): instead of improving the
prompt, add a deterministic pre-check that fires when the answer is already known.

When creating a new session for a known client, query the client's most common
past `category_id` before asking Haiku. If a majority category exists, use it
directly; Haiku's `session_category` hint is consulted only for new clients or
those with no category history. The anchor is implemented in a single helper
`_resolve_category(client_id, haiku_hint)` that all three creation paths call.

### 5. Two-phase deprecation of `spread_type`

`spread_type TEXT` stays in the schema through phases 1–4 to allow rollback.
It is dropped in a separate Alembic migration in phase 5, after the production
read path has been fully switched to `category_id`.

### 6. Reply-update for category (phase 4)

`core/reply_update.py` `_SESSION_REPLY_SYSTEM` will gain a `category` field so
the practitioner can write `это магвоздействия` as a reply to a session card
to set or change `category_id`. The `set_props` method on `PgSessionsRepo` will
resolve the code and write the FK. This covers the NULL→cat case (Vision) and
the wrong→correct case from Haiku drift.

---

## Five-phase rollout plan

| Phase | What | Who | Rollback |
|-------|------|-----|---------|
| **1** | Alembic: `session_category` table + seed + `sessions.category_id` NULLABLE. `sessions_tables.py` updated. | `alembic upgrade` in deploy.sh | `alembic downgrade` — clean drop |
| **2** | Code writes both `spread_type` (old) and `category_id` (new). Readers still use `spread_type`. | deploy | revert to phase-1 code |
| **3** | Backfill: real categories → direct map; shape-trash → client anchor → fallback sphere. Three SQL steps run manually on VPS with `count(*)` checks. | **Кай manually on VPS** | `UPDATE sessions SET category_id = NULL WHERE ...` |
| **4** | All readers switch to `category_id`. Reply-update gains category support. Mini-app, bot header, schema, Notion adapter all read from lookup. | deploy | revert to phase-2 code |
| **5** | Separate Alembic migration: `ALTER TABLE sessions DROP COLUMN spread_type`. Only after explicit go from Кай. | `alembic upgrade` manually | irreversible — requires restore |

---

## Production evidence

All five phases deployed and verified on 2026-06-26:

- **Phase 1–2** (commits 3287755, c4108d1): `session_category` table seeded with 5 rows;
  `sessions.category_id` FK column added; dual-write active.
- **Phase 3** (backfill): 33 existing sessions backfilled — all assigned `category_id → sphere`
  (code `"sphere"`, id=1); client anchor populated for repeat clients.
- **Phase 4** (commit 077d093): bot header shows `🌐 Сфера жизни · Уэйт`; mini-app list
  and by-slug return `category_display` from JOIN; `core/reply_update.py` accepts
  category phrase in reply to session card → writes FK directly.
- **Phase 5** (commit 077d093, migration x4y5z6a7b8c9): `ALTER TABLE sessions DROP COLUMN
  spread_type` applied; `TripletEntry.spread_type` removed; 1601 tests green.

Client anchor observed working: new session for client with prior `sphere` history
inherits `category_id=1` without Haiku hint.

---

## Alternatives considered

**A) Keep `spread_type`, add `category_id` in parallel permanently.**  
Rejected: perpetuates the divergence. Two columns encoding the same dimension
with different semantics is harder to reason about than one migration that
resolves it. The dual-write period (phases 2–4) is transient, not permanent.

**B) Text `CHECK` constraint instead of FK lookup.**  
Rejected: inconsistent with ADR-0008 which was specifically adopted to prevent
emoji/label drift. A CHECK constraint encodes the emoji into the migration itself
rather than into a row that can be updated without schema change.

**C) Improve the Haiku prompt to assign category reliably.**  
Rejected: same reasoning as ADR-0017 (scan-before-spell). Prompt engineering
does not provide a hard guarantee; the same input through the same model can
produce different outputs across API calls. The client-anchor approach is
deterministic for repeat clients, which is the majority case.

---

## Consequences

- **Breaking in phase 4**: `TripletEntry.spread_type` is replaced by
  `category_code: str`. All callers that read `t.spread_type` must be updated
  (see Verify against list above).
- **Tests**: fixtures using `spread_type=` must be updated in phase 2/4.
  `test_session_parser.py`, `test_arcana_mode_a_authored.py`,
  `test_bot_notify.py`, `test_miniapp_arcana.py` are the affected files (see #174).
- **Emoji divergence resolved**: `core/schema.py` options list will be replaced
  by a constant dict mirroring the lookup seed — one source, no drift possible.
- **Vision NULL category**: practitioner-visible, correctable via reply in phase 4.
  The practitioner already corrects card errors; category is no different.
