# LISTS — data-model contract (🗒️ Списки)

Code conforms to: 0bc132e. This spec describes the lists data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

🗒️ Списки holds the user's list items: shopping items, checklists, and
inventory. The domain is split by bot into two PostgreSQL tables —
`nexus_lists` (Nexus: shopping / checklist / inventory) and
`arcana_inventory` (Arcana: practice consumables + barter checklists). Items
can be linked to a parent ✅ Задача (`task_id`) or 🔮 Работа (`works_id`),
and a purchase can post an expense to 💰 Финансы.

## Schema

Two tables. Migration:
`alembic/versions/k1d2e3f4g5h6_nexus_lists_arcana_inventory_pg.py` (revision
`k1d2e3f4g5h6`, down_revision `j0c1d2e3f4g5`). SQLAlchemy Core mirror:
`core/repos/lists_table.py` (column-for-column).

### `nexus_lists`

| Column | Type | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `notion_id` | Text | UNIQUE (nullable) — legacy Notion-migration artifact; not written by the current create path; slated for removal (see #149) |
| `name` | Text | NOT NULL, default `''` |
| `list_type` | Text | NOT NULL, default `'покупки'` |
| `status` | Text | NOT NULL, default `'not_started'` |
| `category` | Text | NOT NULL, default `''` |
| `quantity` | Numeric | nullable |
| `note` | Text | NOT NULL, default `''` |
| `price_actual` | Numeric | nullable (Notion «Цена») |
| `price_plan` | Numeric | nullable (Notion «Цена план») |
| `store` | Text | NOT NULL, default `''` |
| `priority` | Text | NOT NULL, default `''` |
| `group_name` | Text | NOT NULL, default `''` |
| `is_recurring` | Boolean | NOT NULL, default `false` |
| `remind_days` | BigInteger | nullable |
| `expires_at` | Date | nullable |
| `stage` | BigInteger | nullable |
| `task_id` | Text | NOT NULL, default `''` — ✅ Задачи page_id |
| `works_id` | Text | NOT NULL, default `''` — 🔮 Работы page_id |
| `user_notion_id` | Text | NOT NULL, default `''` |
| `created_at` | TIMESTAMP(tz) | default `now()` |
| `updated_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `ix_nexus_lists_list_type`, `ix_nexus_lists_status`,
`ix_nexus_lists_category`, `ix_nexus_lists_group_name`, `ix_nexus_lists_user`,
`ix_nexus_lists_is_recurring`, `ix_nexus_lists_expires_at`.

### `arcana_inventory`

Same shape minus the shopping-specific columns (`price_actual`, `price_plan`,
`store`, `priority`, `stage`, `task_id`); `list_type` defaults to
`'инвентарь'`. Columns: `id`, `notion_id` (legacy, see #149), `name`,
`list_type`, `status`, `category`, `quantity`, `note`, `group_name`
(barter: session/ritual title), `is_recurring`, `remind_days`, `expires_at`,
`works_id`, `user_notion_id`, `created_at`, `updated_at`. Indexes:
`ix_arcana_inventory_list_type`, `…_status`, `…_category`, `…_user`,
`…_expires_at`.

### Enumerated string fields (no FK; PG stores canonical lowercased codes)

The owning maps are in `core/repos/pg_nexus_lists_repo.py`
(`NOTION_TYPE_TO_PG`, `NOTION_STATUS_TO_PG`, `NOTION_PRIORITY_TO_PG`) and the
table column comments in `core/repos/lists_table.py`. Examples below are
non-exhaustive — see those constants for the full set:
- `list_type`: `покупки`, `чеклист`, `инвентарь` (examples, non-exhaustive — see code constants).
- `status`: `not_started`, `in_progress`, `done`, `archived` (examples, non-exhaustive — see code constants).
- `priority` (nexus_lists): `''`, `можно_потом`, `важно`, `срочно` (examples, non-exhaustive — see code constants).
- `category`: free Text; Notion-style emoji codes, e.g. `🕯️ Расходники`, `🔄 Бартер` (examples, non-exhaustive — owned by handlers/`list_manager`).

### Domain objects

`core/repos/pg_nexus_lists_repo.py:ListItem` (nexus_lists) and `InventoryItem`
(arcana_inventory), both `@dataclass`. They surface PG codes directly;
`quantity`/`price_*` as `float|None`, `expires_at`/`date` as `YYYY-MM-DD`
strings (`date`←`created_at[:10]`).

## Operations & contract

Two async repos, `PgNexusListsRepo` and `PgArcanaInventoryRepo`
(`asyncio.to_thread` over sync SQLAlchemy). Public API accepts Notion-style
labels (e.g. `"📦 Инвентарь"`, `"Not started"`) and maps them to PG codes via
the `_pg_type`/`_pg_status`/`_pg_priority` helpers. A higher-level facade
`core/list_manager.py` (and the seam `core/repos/lists_repo.py`) orchestrates
multi-step flows; handlers call the facade, not the repos directly.

- **add** — `add_item(...)` inserts a row and returns the domain object.
  Inputs normalized to PG codes; unknown labels fall back to defaults
  (`покупки` / `not_started`). `PgArcanaInventoryRepo` is the barter store.
- **read** — `get_list(list_type/category, status, user_notion_id)` and
  `search(query, …)` (`name ILIKE %query%`); both default to excluding
  `archived`. Scoped by `user_notion_id` when provided; ordered
  `created_at desc`.
- **update / status** — `update(id, **fields)` and `update_status(id, code)`
  stamp `updated_at = now()`; return `False` if the id resolves to no row.
- **check / purchase** — via `list_manager`/`lists_repo`: `check_items`
  marks items done and, for a shopping purchase, posts one `💸 Расход` to
  💰 Финансы through `FinanceRepo` (`record_purchase`, category mapped by
  `CATEGORY_TO_FINANCE`). `buy_mark_done_by_id(id, price)` sets
  `price_actual` + status done + finance write; `mark_item_done(id)` marks
  done WITHOUT a finance write.
- **relations** — `get_items_for_task(task_id)` /
  `get_items_for_works(works_ids)` fetch children linked by `task_id` /
  `works_id` (used to render subtasks under a task/work). The
  "📋 Подзадачи" flow (`core/subtasks_handler.py`) writes child items here
  with the parent relation set.
- **recurring / expiry** — `get_recurring()` returns `done` +
  `is_recurring` items (re-cloned by `list_manager.clone_recurring`);
  `get_expiry_due(today)` returns inventory rows with `expires_at` set.
- **barter (Arcana)** — `get_open_barter(user_notion_id)` returns
  `arcana_inventory` rows with `category = '🔄 Бартер'` not yet done/archived.

## Invariants

- **Barter category is Arcana-only.** `'🔄 Бартер'` (`BARTER_CATEGORY`) is
  written only to `arcana_inventory`; `barter_prompt.py` routes there, never
  to `nexus_lists`. Enforced by routing, asserted in the table comment.
- **Status set** is `not_started` / `in_progress` / `done` / `archived`
  (owned by `NOTION_STATUS_TO_PG`).
- **Archived rows are excluded from default reads** (`search`, `get_list`,
  summary, group/relation queries all filter `status != 'archived'` unless an
  explicit status is requested).
- **Item↔task/work link is by stored page_id string** (`task_id` /
  `works_id`), not an FK; relation reads also include rows with empty
  `user_notion_id` (shared/legacy).
- **`group_name` groups items** within a list_type (checklist groups; for
  barter it carries the session/ritual title).

## Lifecycle / status model

```
add → not_started ──update_status──▶ in_progress ──▶ done ──▶ archived
done + is_recurring ──clone_recurring──▶ new not_started item (next cycle)
```

`done` is terminal for a one-off item; recurring items are re-cloned rather
than reopened. `archived` hides an item from all default reads.

## Callers

- Facade/seam — `core/list_manager.py`, `core/repos/lists_repo.py`
  (re-exports + PG writes sealed from handlers).
- Bots — `nexus/handlers/lists.py`, `arcana/handlers/lists.py`,
  `arcana/handlers/barter_prompt.py` (barter), `arcana/handlers/ritual_writeoff.py`
  (inventory write-off), `core/subtasks_handler.py` (checklist children).
- Cross-domain — `core/cash_register.py` (open-barter count),
  `nexus/handlers/finance.py` (purchase → expense), `core/classifier.py`.
- Mini App — `miniapp/backend/routes/lists.py` (`GET /api/lists`),
  `arcana_inventory.py`, `arcana_barter.py`, `writes.py`, `categories.py`,
  `arcana_today.py`.
- Backfill — `scripts/backfill_lists.py` (Notion → PG, uses `notion_id`).

## Model routing (from code)

Lists parsing is Haiku-only (`claude-haiku-4-5-20251001`):
`core/lists_parser.py:parse_buy_text`, `nexus/handlers/lists.py`,
`arcana/handlers/lists.py` (extracting items/quantities/categories from free
text). No Sonnet, no Opus. Reads/writes/status are pure SQL.

## Verify against code

- `alembic/versions/k1d2e3f4g5h6_nexus_lists_arcana_inventory_pg.py` — tables + indexes
- `core/repos/lists_table.py` — SQLAlchemy Core definitions + column comments
- `core/repos/pg_nexus_lists_repo.py` — `ListItem`/`InventoryItem`, value maps,
  sync helpers, `PgNexusListsRepo`/`PgArcanaInventoryRepo`, barter guard
- `core/repos/lists_repo.py` — seam (`ListsRepo`, `record_purchase`, PG writes)
- `core/list_manager.py` — add/check/checklist/inventory/recurring/expiry flows
- `core/lists_parser.py` — Haiku buy-text parser
- `nexus/handlers/lists.py`, `arcana/handlers/lists.py` — bot handlers
- `arcana/handlers/barter_prompt.py`, `arcana/handlers/ritual_writeoff.py`
- `core/subtasks_handler.py` — checklist children (task/works relation)
- `core/cash_register.py` — open-barter count
- `miniapp/backend/routes/lists.py`, `arcana_inventory.py`, `arcana_barter.py`
- `scripts/backfill_lists.py` — Notion → PG backfill
