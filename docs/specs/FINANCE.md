# FINANCE — data-model contract (💰 Финансы)

Code conforms to: 0bc132e. This spec describes the finance data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants. Enumerations point at the owning code
> constant rather than restating it.

## Purpose

💰 Финансы is the money ledger, shared by both bots but split by bot into two
PostgreSQL tables — `nexus_budget` (Nexus personal finance: income, expenses,
salary) and `arcana_pnl` (Arcana practice P&L: practice income, costs,
barter). A single facade (`FinanceRepo`) routes writes/reads to the right
table by `bot_label`. Money records are the source rows for budget
aggregation (see BUDGET.md) and the Arcana P&L (`core/cash_register.py`).

## Schema

Two tables, identical shape. Migration:
`alembic/versions/l2e3f4g5h6i7_finance_pg.py` (revision `l2e3f4g5h6i7`,
down_revision `k1d2e3f4g5h6`). SQLAlchemy Core mirror:
`core/repos/finance_table.py` (column-for-column). Neither table has a
`notion_id` column.

### `nexus_budget` and `arcana_pnl`

| Column | Type | Constraints / default |
|---|---|---|
| `id` | BigInteger | PK, autoincrement |
| `description` | Text | NOT NULL, default `''` |
| `amount` | Numeric(12, 2) | NOT NULL, default `0` |
| `category` | Text | NOT NULL, default `''` |
| `type_` | Text | NOT NULL, default `''` |
| `source` | Text | NOT NULL, default `''` |
| `date` | Date | nullable |
| `user_notion_id` | Text | NOT NULL, default `''` |
| `created_at` | TIMESTAMP(tz) | default `now()` |

Indexes: `ix_nexus_budget_date`, `ix_nexus_budget_type_` (and the analogous
`ix_arcana_pnl_date`, `ix_arcana_pnl_type_`).

### Enumerated string fields (free Text; no FK)

Owned by the writer code and the column comments in
`core/repos/finance_table.py`. Examples, non-exhaustive — see the code:
- `type_`: `💰 Доход`, `💸 Расход` (examples, non-exhaustive — see column comment).
- `source`: `💳 Карта`, `💵 Наличные`, `🔄 Бартер` (Arcana only), … (examples, non-exhaustive — see column comment and the barter guard).

### Domain objects

`core/repos/pg_finance_repo.py:BudgetEntry` (nexus_budget) and `PnlEntry`
(arcana_pnl). The facade `core/repos/finance_repo.py` exposes a unified
`FinanceEntry` with a `bot` tag (`"☀️ Nexus"` / `"🌒 Arcana"`); `amount` is
`float`, `date` is `YYYY-MM-DD`.

## Operations & contract

`FinanceRepo` (`core/repos/finance_repo.py`, singleton `_repo`) is the
canonical entry point; `PgNexusBudgetRepo`/`PgArcanaPnlRepo` are the
per-table implementations.

- **add / create_entry** — `add(*, date, amount, category, type_, source,
  bot_label, description, user_notion_id)` routes by `bot_label`
  (`_is_arcana`) to `arcana_pnl` or `nexus_budget` and returns the new id.
  `create_entry(db_id, …)` is a back-compat wrapper; `db_id` is ignored.
  Barter guard applies (see Invariants).
- **read by range** — `query_records(*, date_from, date_to, type_, category,
  page_size, user_notion_id)` unions both tables, sorts `date desc`, trims to
  `page_size`. `month(month, user_notion_id, description_filter, type_filter)`
  returns all rows for `YYYY-MM` from both tables. Per-table `query` /
  `query_month` accept `type_filter` ∈ {`expense`→`%Расход%`,
  `income`→`%Доход%`, exact}.
- **search** — `PgNexusBudgetRepo.search_description(text, …)`
  (`description ILIKE %text%`).
- **update** — `update_field(id, field, value)` updates one of
  {`source`, `category`, `description`, `type_`, `amount`} on a row (tries
  nexus_budget then arcana_pnl). `update_last(target_type, field, value)`
  edits the most recent expense/income row.

There is no delete operation in the finance repos/seam — the ledger is
append-plus-edit; rows are not removed in code.

## Invariants

- **Barter is Arcana-only.** `source = '🔄 Бартер'` (`BARTER_SOURCE`) is
  valid only for `arcana_pnl`. `FinanceRepo._guard_source` sanitizes a barter
  source to `'💳 Карта'` for any non-Arcana write (logged). There is no
  barter concept in `nexus_budget`.
- **Reads are fail-closed per user.** All per-table query helpers return `[]`
  when `user_notion_id` is empty — finance data is never aggregated across
  users (#139).
- **`type_` is the income/expense axis** (`💰 Доход` / `💸 Расход`); the
  `expense`/`income` filters match by substring (`%Расход%` / `%Доход%`).
- **Arcana P&L is derived, not stored.** `core/cash_register.py` computes P&L
  as Arcana income − Arcana expense − salary payouts, where salary payouts are
  `nexus_budget` rows with `type_ = 💰 Доход` and `category = 💰 Зарплата`
  (`SALARY_CATEGORY`); there is no P&L summary column.
- **List purchases post here.** A shopping purchase writes one `💸 Расход`
  via `FinanceRepo` (see LISTS.md `record_purchase`); 💰 Финансы does not call
  back into Lists.

## Lifecycle / status model

A finance record has no status/soft-delete flags. Lifecycle is: created
(routed to one table by bot) → optionally field-edited in place. It is never
archived or deleted by code; `date` (the accounting day) and `created_at`
(insert time) are independent.

## Callers

- Bots — `nexus/handlers/finance.py` (income/expense entry, edits),
  `arcana/handlers/finance.py`, `arcana/handlers/barter_prompt.py`,
  `arcana/handlers/rituals.py`.
- Cross-domain — `core/cash_register.py` (P&L), `core/list_manager.py` /
  `core/repos/lists_repo.py` (purchase → expense), `core/classifier.py`,
  `core/memory.py`, `nexus/handlers/tasks.py`.
- Mini App — `miniapp/backend/routes/finance.py` (`GET /api/finance`,
  today/month/limits/goals views), `arcana_finance.py`
  (`GET /api/arcana/finance/pnl`, `POST /api/arcana/finance/pay_salary`),
  `writes.py`, `today.py`, `arcana_today.py`.
- Backfill — `scripts/backfill_finance.py` (Notion → PG).

## Model routing (from code)

Parsing a finance entry from free text is Haiku
(`arcana/handlers/finance.py`; `nexus/handlers/finance.py` uses
`config.model_haiku`). The on-demand budget recalculation narrative uses
Sonnet (`config.model_sonnet`, `BUDGET_SONNET_SYSTEM`) — documented in
BUDGET.md. Ledger reads/writes are pure SQL.

## Verify against code

- `alembic/versions/l2e3f4g5h6i7_finance_pg.py` — tables + indexes
- `core/repos/finance_table.py` — SQLAlchemy Core definitions + column comments
- `core/repos/pg_finance_repo.py` — `BudgetEntry`/`PnlEntry`,
  `PgNexusBudgetRepo`/`PgArcanaPnlRepo`, `_type_cond`, fail-closed reads
- `core/repos/finance_repo.py` — `FinanceRepo` facade, `FinanceEntry`,
  `_guard_source` barter guard, union reads
- `nexus/handlers/finance.py`, `arcana/handlers/finance.py` — entry/edit, model routing
- `core/cash_register.py` — Arcana P&L derivation (salary category)
- `core/repos/lists_repo.py` — `record_purchase` (list purchase → expense)
- `miniapp/backend/routes/finance.py`, `arcana_finance.py` — finance views, pay_salary
- `scripts/backfill_finance.py` — Notion → PG backfill
