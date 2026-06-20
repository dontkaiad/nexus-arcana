# BUDGET — data-model contract (бюджет / day limit)

Code conforms to: 0bc132e. This spec describes the budget data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the derived model and the guarantees of
> each operation. Enumerations point at the owning code constant rather than
> restating it.

## Purpose

Budget is a **derived planning view**, not a stored table. It is computed
on demand from existing data: budget facts in Memory (the `memories` table)
plus active debts (the `debts` table). `core/budget.py` is the shared layer
behind the Nexus `/budget` command and the Mini App finance/day views; it
turns saved plan facts into per-category limits and a single daily spend
limit.

## Schema

Budget has **no table of its own** — there is no migration, no
`*_table.py`, no repo for "budget". It reads from:

1. **`memories`** (see MEMORY.md) — budget facts identified by `key_name`
   prefix and category. The prefix↔category map is owned by
   `core/budget.py:BUDGET_KEY_TO_CATEGORY`. Examples, non-exhaustive — see
   that constant:
   - `income_` → `📥 Доход`; `обязательно_` → `🔒 Обязательные`;
     `лимит_` → `💰 Лимит`; `цель_` → `🎯 Цели`; `долг_` → `📋 Долги`.
   - the payday is a single Memory fact at exact key `budget_payday`
     (default `1` if absent).
   Amounts are parsed from the fact text by the regexes in `core/budget.py`
   (`LIMIT_AMOUNT_RE`, `INCOME_RE`, `OBLIGATORY_RE`, `GOAL_RE`).
2. **`debts`** (see the debts domain, `core/repos/pg_debts_repo.py`) — active
   debts with `kind='i_owe'`; the fields consumed are `name`, `amount`,
   `deadline`, `strategy`, `monthly_payment`. Debts come from this table, not
   from Memory.

The limit display map (`лимит` link → emoji label) is owned by
`core/budget.py:LIMIT_DISPLAY` (examples, non-exhaustive).

## Operations & contract

All in `core/budget.py` (pure async functions; no repo class):

- **get_limits()** → `{cat_link: amount}` — reads current `💰 Лимит`
  memories (`find_by_category`), extracts the category link and amount per
  fact. Skips facts where link or amount can't be parsed.
- **load_budget_data(user_notion_id)** → `{"доходы", "обязательные", "цели",
  "долги", "лимиты"}` — reads budget memories by key prefix
  (`find_by_key_prefixes(["income_", "обязательно_", "лимит_", "цель_"])`,
  current rows only) plus active `i_owe` debts; parses each into a list of
  `{name, amount, …}` dicts. Limits are de-duplicated by display name (the
  higher amount wins).
- **budget_day_limit_from_plan(user_notion_id)** → `int` — the daily spend
  limit from the saved plan (see Invariants for the exact formula). Returns
  `0` when there is no income or on any error.

## Invariants

- **Day-limit formula** (`budget_day_limit_from_plan`), exactly as coded:
  ```
  free = total_income
       − total_obligatory
       − total_limits
       − total_goals_saving        # sum of цели[].saving
       − total_debt_monthly        # sum of долги[].monthly_payment > 0
  day_limit = max(0, int(free / days_remaining))
  ```
  where `total_income = sum(доходы[].amount)`; if `total_income <= 0` the
  function returns `0` immediately.
- **`days_remaining`** comes from `_period_days_remaining(payday)`: days from
  today (00:00 MSK, `_MOSCOW_TZ = UTC+3`) to the day before the next payday,
  floored at `1`. `payday` is read from Memory key `budget_payday`
  (`_budget_payday`, default `1`).
- **Debts are sourced from the `debts` table**, not Memory; only
  `kind='i_owe'`, and only `monthly_payment > 0` contributes to the daily
  formula.
- **Limits de-duplicate by display name** (`display_limit_name` via
  `LIMIT_DISPLAY`); the larger amount is kept.
- **Computation is stateless.** Each call recomputes from current Memory +
  debts; no computed budget is persisted.

## Lifecycle / status model

No lifecycle — budget is recomputed per request. The underlying facts follow
their own stores' lifecycles: budget memories are soft-deleted/updated per
MEMORY.md; debts per the debts domain. The Nexus `/budget` command renders
the saved plan and only recomputes the Sonnet analysis on explicit user
action (see Model routing).

## Callers

- Nexus — `nexus/handlers/finance.py` (`/budget`: `get_limits`,
  `load_budget_data`; the message renders the saved plan), `nexus/nexus_bot.py`.
- Mini App — `miniapp/backend/routes/finance.py` (`get_limits`,
  `load_budget_data`, `budget_day_limit_from_plan` for limit/goal views) and
  `miniapp/backend/routes/today.py` (`budget_day_limit_from_plan` for the day
  limit).

## Model routing (from code)

`core/budget.py` itself uses **no LLM** — it is regex parsing plus
arithmetic. The default `/budget` message is built from the saved plan
without Sonnet (`nexus/handlers/finance.py` comment: "НЕ вызывает Sonnet").
The on-demand recalculation/advice (triggered by a button) uses Sonnet
(`config.model_sonnet`, `BUDGET_SONNET_SYSTEM`,
`nexus/handlers/finance.py:start_budget_analysis`). Sonnet is justified here
for long-form budget reasoning; the routine path stays LLM-free.

## Verify against code

- `core/budget.py` — `BUDGET_KEY_TO_CATEGORY`, `LIMIT_DISPLAY`, regexes,
  `get_limits`, `load_budget_data`, `_budget_payday`,
  `_period_days_remaining`, `budget_day_limit_from_plan`
- `core/repos/memory_repo.py` / `core/repos/pg_memory_repo.py` —
  `find_by_category`, `find_by_key_prefixes`, `find_by_exact_key` (budget facts)
- `core/repos/pg_debts_repo.py` — active `i_owe` debts read by `load_budget_data`
- `nexus/handlers/finance.py` — `/budget` render, `start_budget_analysis`,
  `BUDGET_SONNET_SYSTEM` (Sonnet on recalc only)
- `nexus/nexus_bot.py` — `/budget` wiring
- `miniapp/backend/routes/finance.py` — limits/goals views, day limit
- `miniapp/backend/routes/today.py` — `budget_day_limit_from_plan` (day limit)
- `docs/specs/MEMORY.md` — budget facts live in the `memories` table
