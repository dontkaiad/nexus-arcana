# MEMORY — memory data model

> **Status: AS-BUILT, code conforms to `720f975`.** Notion→PostgreSQL
> migration is complete; the semantic-search layer (ADR-0006 pgvector
> backend, applied to memory by ADR-0020) has landed. Update this spec in
> the same PR that changes the memory schema or search strategy.

> Source of truth is the code, not Notion specs. Every statement is
> verifiable against the files in the "Verify against code" section at the
> end. Where the code diverges from ADR-0005/ADR-0020, the CODE is
> documented and the divergence is flagged explicitly.

## Purpose

Memory is the long-term store of facts about the user and their surroundings,
shared by both bots (Nexus + Arcana). It holds short textual assertions with a
category, a key tag, and a relation to a person/object.

What it holds (categories, `core/memory.py:CATEGORIES`, 15 items):
`🦋 СДВГ`, `👥 Люди`, `🏥 Здоровье`, `🛒 Предпочтения`, `💼 Работа`,
`🏠 Быт`, `🔄 Паттерн`, `💡 Инсайт`, `🔮 Практика`, `🐾 Коты`,
`💰 Лимит`, `🔒 Постоянные`, `📥 Доход`, `📋 Долги`, `🎯 Цели`.

Boundary "memory about the user" vs "domain knowledge":
- Memory — about the user and related people/objects (preferences, patterns,
  ADHD adaptations, notes about people and cats).
- Domain knowledge (the Arcana grimoire, Tarot cards, etc.) — NOT memory, it
  lives in its own domain tables. There are no domain entities in the memory
  code.
- Budget configuration (limits/income/obligatory/goals/debts) physically
  lives in the same `memories` table under category `💰 Лимит` and keys with
  prefixes `лимит_`/`постоянно_`/`цель_`/`долг_`/`income_`, read via a
  separate path (`core/budget.py`). ADR-0005 marks this as a "parked
  follow-up" — a candidate for extraction into the finance module; in the
  code it is NOT extracted yet.

## Schema (as built, from the migration)

A single `memories` table (PostgreSQL). Alembic migration
`alembic/versions/j0c1d2e3f4g5_core_memories_pg.py`, revision `j0c1d2e3f4g5`,
down_revision `i9d0e1f2g3h4`. SQLAlchemy Core mirror —
`core/repos/memories_table.py` (matches column-for-column).

| Column | Type | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `notion_id` | Text | UNIQUE (nullable) |
| `fact_text` | Text | NOT NULL |
| `key_name` | Text | NOT NULL, default `''` |
| `value_text` | Text | NOT NULL, default `''` |
| `category` | Text | NOT NULL, default `''` |
| `scope` | Text | NOT NULL, default `'global'` |
| `source` | Text | NOT NULL, default `'manual'` |
| `related_to` | Text | NOT NULL, default `''` |
| `is_current` | Boolean | NOT NULL, default `true` |
| `is_archived` | Boolean | NOT NULL, default `false` |
| `user_notion_id` | Text | NOT NULL, default `''` |
| `created_at` | TIMESTAMP(tz) | default `now()` |
| `updated_at` | TIMESTAMP(tz) | default `now()` |
| `embedding` | vector(1024) | nullable |

Indexes (from the migration):
`ix_memories_key_name` (key_name), `ix_memories_category` (category),
`ix_memories_scope` (scope), `ix_memories_is_current` (is_current),
`ix_memories_user` (user_notion_id).

`embedding` was added by a second migration
(`alembic/versions/y5z6a7b8c9d0_memories_embedding_pgvector.py`, down_revision
`x4y5z6a7b8c9`) with its own `hnsw (embedding vector_cosine_ops)` index
(`idx_memories_embedding`) and a `CREATE EXTENSION IF NOT EXISTS vector`
shared with `arcana_triplets` (ADR-0006's table — the first pgvector
consumer). This column is intentionally **not** declared on the
`core/repos/memories_table.py` SQLAlchemy Core `Table` object — all reads/
writes of it go through raw SQL in `core/memory_rag.py`, the same pattern
`core/rag.py` uses for `arcana_triplets`. Nullable: existing rows have no
embedding until `scripts/migrate_memory_embeddings.py` backfills them.

Domain object `Memory` (`core/repos/pg_memory_repo.py`,
`@dataclass`) maps a row: `id` (str), `fact`←fact_text, `key`←key_name,
`value`←value_text, `category`, `scope`, `source`, `related_to`←related_to,
`is_current`, `is_archived`, `user_notion_id`, `date`←created_at[:10],
`updated_at`←ISO.

Field values as actually used in the code:
- `scope` ∈ {`global`, `nexus`, `arcana`}. bot_label→scope mapping:
  `☀️ Nexus`→`nexus`, `🌒 Arcana`→`arcana`, otherwise `global`
  (`pg_memory_repo.bot_to_scope`).
- `source` ∈ {`manual`, `auto`}. `core/memory.py:save_memory` and
  `MemoryRepo.save_parsed` (auto-suggest confirm) both hardcode `"manual"`
  regardless of how the fact was captured (see #148); `core/location.py`
  is the one path that actually writes `source="auto"`.
- `notion_id` in a normal write = `None`; the `notion_id` parameter of `add`
  is used only by the backfill `scripts/backfill_memories.py` (mapping to
  old Notion records).

## How it works

### Layers
`handlers → core/memory.py → core/repos/memory_repo.py (_repo) →
core/repos/pg_memory_repo.py → memories_table (PG)`.
`memory_repo.py` — a thin seam over `PgMemoryRepo`; singleton `_repo`.
All sync SQL is wrapped in `asyncio.to_thread`.

### Write
`core/memory.py:save_memory(message, text, user_notion_id, bot_label)`:
1. `maybe_convert` (EN→RU keyboard layout).
2. `_parse_fact` — Haiku (`claude-haiku-4-5-20251001`, temperature=0,
   max_tokens=200) → `(fact, category, связь, ключ)`. Invalid category →
   `💡 Инсайт`; full parse failure → fallback
   `(текст, "💡 Инсайт", "", "факт")`.
3. `scope = bot_to_scope(bot_label)`.
4. For non-limit facts with `связь` — `_resolve_alias`: canonicalize the name
   through already-saved records (regex patterns for nicknames/aliases,
   depth ≤3, cycle protection).
5. Write:
   - `category == "💰 Лимит"` and `ключ` present → `_repo.upsert` (find by
     `key_name`+`category` among non-archived, update; else create).
     Returns `(id, was_updated)`.
   - otherwise → `_repo.add` (always INSERT a new row).
6. Side-effect: for category `🦋 СДВГ` and a new record — `_get_adhd_tip`
   (Sonnet, `config.model_sonnet`, temperature=0.7) sends a tip.
7. For the plain-fact reply branch (not a budget key), the confirmation
   message is registered in `message_pages` (`page_type="memory"`,
   `bot=scope`) so a reply on it can later correct the record (see Reply
   corrections below).

Write contract: `value_text` is not populated by any write path — for readers
it is always `''` (the fact value lives in `fact_text`) (see #146).

### Embedding indexing (ADR-0020)

Every write that reaches `PgMemoryRepo.add`/`.upsert` — not just
`save_memory()` — spawns a fire-and-forget `asyncio` task
(`core/repos/pg_memory_repo.py:_spawn_index`) that embeds
`fact_text + " " + related_to + " " + category` via Voyage
(`voyage-4-lite`, dim 1024, client shared with `core/rag.py`) and writes it
to the `embedding` column. This covers `save_memory()`, auto-suggest
confirm (`save_parsed`), budget upserts (`_save_memory_entry`), and
`core/location.py:set_user_location` uniformly, because the hook lives at
the repo layer, not in any one caller.

`upsert`'s update-existing-row path first `NULL`s the row's `embedding` in
its own try/except'd transaction (separate from the field-update
transaction) before the reindex task fires — a changed fact must not keep
matching on its old text, and a missing `embedding` column (unmigrated
checkout) must not roll back the primary write.

`PgMemoryRepo.update_fields(memory_id, fact=, category=, related_to=)` is a
point-update by `id` (distinct from `upsert`, which matches by
`key_name`+`category`) used by the reply-correction path; it re-embeds the
row's current values the same way.

### Reply corrections (#188)

A reply to a "🧠 Запомнил …" plaque is parsed by
`core/reply_update.py`'s `page_type="memory"` schema into
`{move_to_notes, fact, category}`:
- `fact`/`category` set → `PgMemoryRepo.update_fields` patches the row
  in place (`core/reply_update.py:_apply_memory`).
- `move_to_notes=true` — handled by the bot-specific handler *before*
  `apply_updates` (it's a cross-domain move, not a field patch): Nexus
  archives the memory row and creates a `📝 Заметки` entry from the
  plaque's own text (`nexus/handlers/reply_update.py:_move_memory_to_notes`);
  Arcana has no notes feature, so the same reply gets an explicit
  "нет заметок" message instead of falling through to `unknown`.

Budget-key branches (`постоянно_`/`цель_`/`долг_`/limit facts) are not
registered for reply-correction — they're already editable via `/budget`.

### Read
Two modes:

1. Exact key — `find_by_exact_key(key, user_notion_id, page_size)`:
   `key_name == key` (strict equality), `is_current=True`,
   `is_archived=False`, sorted by `updated_at desc`. Actual calls:
   `tz_{tg_id}` (timezone — `core/shared_handlers.py`,
   `nexus/handlers/tasks.py`, `miniapp/.../weather.py`),
   `budget_payday` (`nexus/handlers/finance.py`).
2. Substring search — `search(terms, scope, user_notion_id, page_size)`:
   `OR` of `ILIKE %term%` over `fact_text`, `key_name`, `related_to`;
   activity filter (`is_current=True`, `is_archived=False`); optional
   `scope` (match OR `global`) and `user_notion_id`; sorted by
   `created_at desc`.

### Semantic fallback (ADR-0020)

`search`/`_find_pages_by_hint` results are ILIKE-only by construction (see
above) — semantics is layered on top by the *caller*, in
`core/memory.py:_semantic_search_memory`, not inside the repo query.
Contract: **ILIKE-first, semantic only as a thin fallback** — a semantic
query (`core/memory_rag.py:search_memory_semantic`, Voyage cosine over
`embedding`) only fires when the ILIKE result has fewer than 3 rows,
merged after the ILIKE hits and deduped by id, capped at the caller's page
size. Both `search_memory` (`/memory`) and `get_memories_for_context`
(prompt injection, via `_find_pages_by_hint`) use this; `_resolve_alias`
opts out entirely (`use_semantic=False` — alias recall must stay exact,
not "closest match"), as do the destructive flows `deactivate_memory` and
`delete_memory` (they act on every/the-only found row without
confirmation, so a nearest-neighbor false positive would silently
deactivate/archive the wrong fact).

`search_memory_semantic` accepts an optional `min_score` cosine-similarity
cutoff, but **no caller passes one yet** (#185 open) — every call today
returns its raw top-k nearest neighbors, and logs the score distribution
instead of filtering, so a real cutoff can be picked from logged
production data rather than guessed.

Derived reads:
- `find_by_category(category, is_current, scope, user_notion_id, page_size)`
  — exact category match (empty `category` = no category filter).
- `find_by_key_prefixes(prefixes, user_notion_id)` — `key_name ILIKE p%`;
  used by the budget (`core/budget.py`, prefixes `income_`,
  `постоянно_`, `лимит_`, `цель_`).
- `find_recent(is_current, scope, user_notion_id, page_size)` — the latest
  non-archived ones.

`core/memory.py:_find_pages_by_hint` on top of `search`: shortcut by category
name (`сдвг`/`люди`/…→category, via `find_by_category`), otherwise
tokenizes the hint (stop words + naive stemming `_normalize_word`) → `search`.

### Record lifecycle (soft-delete, two flags)
- `is_current` — "currency". `deactivate_memory` → `set_active(ids, False)`
  (`_pg.set_current`), `is_current=False`. The record stays in search
  results but is marked "(неактуально)"; it can be restored (reactivate).
- `is_archived` — "deletion". `delete_memory` → `archive(id)`,
  `is_archived=True`. Archived records are excluded from all reads
  (`_base_active_q` filters `is_archived == False`). There is no hard row
  delete in the code.

### Callers
- Bots, memory handlers: `nexus/handlers/memory.py`,
  `arcana/handlers/memory.py` — save / search / deactivate / delete /
  auto_suggest (inline yes/no).
- Prompt context: `get_memories_for_context(user_notion_id,
  keywords, bot_label, max_results)` — filters by scope (keeps a scope
  match OR `global`), returns a text block "Контекст из памяти:". Called by
  `arcana/handlers/sessions.py`, `clients.py`, `rituals.py`.
- Auto-save: `core/location.py:set_user_location` (`tz_`/`city_` on a
  resolved location) — the sole location writer (ADR-0016). It used to be
  double-called from `core/classifier.py`'s `timezone_update` branch
  together with a separate raw-text `save_memory()`, producing two
  uncoordinated rows from one message; the duplicate call was removed.
- Budget: `core/budget.py` via `find_by_key_prefixes`.
- Recall by word: `recall_from_memory(keyword)` (Nexus finance/tasks) — ILIKE
  only, no semantic fallback (synchronous, called inline during parsing of
  other flows; latency-sensitive).
- Reply corrections: `core/reply_update.py` (`page_type="memory"`),
  `nexus/handlers/reply_update.py`, `arcana/handlers/reply_update.py`.
- Mini App (PG-native, `PgMemoryRepo` directly):
  `miniapp/backend/routes/memory.py` — `GET /api/memory` (excludes
  budget/ADHD categories) and `GET /api/memory/adhd` (grouping
  patterns/strategies/triggers/specifics + Sonnet profile);
  `miniapp/.../weather.py` (timezone via `find_by_exact_key`).

### Model routing (from the code, not from memory)
- Haiku `claude-haiku-4-5-20251001` — `_parse_fact` (parsing a fact on save).
- Sonnet `claude-sonnet-4-6` (`config.model_sonnet`) —
  `core/memory.py:_get_adhd_tip` (tip when saving an ADHD fact) and
  `miniapp/backend/routes/memory.py:_generate_adhd_profile` (ADHD profile).
- Read/search/deactivate/archive — no LLM (pure SQL).

## Key decisions and trade-offs (ADR-0005)

1. **Storage: PG, not Notion.** Memory moved to PG (migration
   `j0c1d2e3f4g5`). Cost: a live PG engine is required (obtained from
   `arcana.repos.pg_sessions_repo.get_engine`), and the human-readability of
   the Notion table is lost. The parallel Notion write path that used to
   exist in `nexus/handlers/finance.py:_save_memory_entry` is gone (#145
   closed) — all memory writes go through PG.

2. **`scope` instead of a `Бот` field.** A single `scope` column
   (`global`/`nexus`/`arcana`) replaced the Notion select `Бот`. Why:
   most facts are shared (`global`), and a rare bot-specific fact does not
   require splitting memory across domains/tables. Cost: scope filtering is
   application logic in every read (`scope == X OR scope == global`), not a
   hard split.

3. **Soft-delete instead of deletion.** Two flags `is_current` (currency,
   reversible) and `is_archived` (hiding from results). Why: history is not
   lost, "стало неактуальным" can be brought back. Cost: rows accumulate,
   every read carries an activity filter; there is no real space reclamation.

4. **facts/observations split — NOT implemented (divergence with ADR-0005).**
   ADR-0005 (Decision) prescribes TWO tables: `facts` (exact
   key→value) and `observations` (free text + category + semantics).
   In reality a SINGLE table `memories` was created with both sets of fields
   (`key_name`/`value_text` AND `fact_text`/`category`) — exactly the
   "unified memory table" that the ADR MARKED as rejected in the Alternatives
   section. There are NO `facts`/`observations` tables in the code/migrations.
   Trade-off as built: simpler (one table, one repository), but two access
   patterns (exact key vs contains-search) are mixed in one place — exactly
   the downside the ADR wanted to avoid. The degenerate artifact of this
   decision is the unpopulated `value_text` (see #146).

5. **Semantic layer: `embedding` column on `memories`, not a mirror table
   (ADR-0020).** `arcana_triplets` (ADR-0006) needed a separate table
   because triplets are a derived read-model over `sessions`; a memory row
   is already the atomic fact-level unit, so a column keeps embedding and
   fact always in sync through the same `upsert`/`update_fields` write
   path instead of risking a second table drifting out of step. Cost: two
   pgvector consumers now share one Voyage free-tier budget (3 RPM),
   mitigated by the ILIKE-first hybrid search strategy (see Read above)
   rather than querying Voyage on every search.

---

Verify against code:
- `alembic/versions/j0c1d2e3f4g5_core_memories_pg.py` — table migration
- `alembic/versions/y5z6a7b8c9d0_memories_embedding_pgvector.py` —
  `embedding` column + hnsw index migration
- `core/repos/memories_table.py` — SQLAlchemy Core definition of `memories`
  (no `embedding` — see Schema above)
- `core/repos/pg_memory_repo.py` — `Memory` dataclass + sync SQL + async API
  (`add`/`upsert`/`update_fields` + `_spawn_index`/`_index_embedding_safe`)
- `core/repos/memory_repo.py` — seam repository, singleton `_repo`
- `core/memory.py` — save/search/deactivate/delete/recall/context,
  `_parse_fact` (Haiku), `_get_adhd_tip` (Sonnet), `CATEGORIES`,
  `_semantic_search_memory`
- `core/memory_rag.py` — `index_memory`/`index_memories_batch`/
  `search_memory_semantic` (Voyage + pgvector, reuses `core/rag.py`'s client)
- `core/reply_update.py` — `page_type="memory"` parse/apply
- `nexus/handlers/reply_update.py`, `arcana/handlers/reply_update.py` —
  reply dispatch, `_move_memory_to_notes` (Nexus only)
- `scripts/migrate_memory_embeddings.py` — embedding backfill (dry-run default)
- `core/budget.py` — budget reads via `find_by_key_prefixes`
- `core/location.py` — sole location writer (`set_user_location`, ADR-0016)
- `core/shared_handlers.py`, `nexus/handlers/tasks.py` — `find_by_exact_key("tz_…")`
- `nexus/handlers/finance.py` — `find_by_exact_key("budget_payday")`,
  `_save_memory_entry` (PG upsert)
- `nexus/handlers/memory.py`, `arcana/handlers/memory.py` — memory handlers
- `arcana/handlers/sessions.py`, `clients.py`, `rituals.py` —
  `get_memories_for_context`
- `miniapp/backend/routes/memory.py` — `GET /api/memory`, `/api/memory/adhd`
- `miniapp/backend/routes/weather.py` — timezone via `find_by_exact_key`
- `core/config.py` — `MODEL_HAIKU`, `MODEL_SONNET` (`claude-sonnet-4-6`)
- `docs/CASES/0005-memory-store.md` — ADR (code diverges: see the section above)
- `docs/CASES/0020-memory-rag.md` — ADR for the semantic layer
