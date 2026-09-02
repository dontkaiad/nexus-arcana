# ADR-0022: Budget arithmetic moves out of the LLM prompt; cushion split from goals

## Status
Accepted (implemented across `core/budget.py`, `nexus/handlers/finance.py`,
`core/repos/pg_cushion_repo.py`; feature commits `a666d86`, `5f582d8`, `c51ede9`,
`7f9d734`, and this one).

## Context

The `/budget` flow takes a free-text financial dump (income, fixed costs, one-off
expenses, debts, goals) and produces a monthly plan: per-category spending limits,
an impulse allowance, a savings figure, and — in a hard month — an A/B fork
(pay the debt in full vs. renegotiate a smaller payment).

The first implementation did **all of this inside one Sonnet system prompt**. The
prompt carried the full distribution algorithm in prose: iron minimums, a
percentage ceiling on the habits category, a fixed priority chain for the leftover
pool ("Кафе → Бьюти → Здоровье → Гардероб → Хобби, each takes what the previous
one left"), a `products_min = max(3000, habits / 2)` rule, and the tight-month
variant logic. The model was asked to return the final numbers.

Two failure classes made this untenable:

1. **Non-determinism on identical input.** The same dump, re-submitted, produced
   **different limit amounts** — habits at 12 000 one run and 13 500 the next,
   the leftover split landing differently each time, occasionally a category
   silently at 0 that had money the run before. At temperature 0 the arithmetic
   still drifted because the model is doing multi-step mental math over a dozen
   numbers and a branching ruleset; small rounding and ordering choices compound.
   For a tool whose entire job is "here is exactly how much you can spend," a plan
   that changes when you press the button again is not usable — the user cannot
   tell a real recalculation from noise.

2. **Prompt rules that fight each other.** Each new constraint (a ceiling, a
   priority order, a per-variant exception, a "never zero products" guard) was
   another paragraph the model had to hold simultaneously. Adding the habits
   ceiling regressed the leftover split; fixing the split regressed the tight-month
   variant. The commit history on the prompt is a firefighting record.

The distribution rules themselves are **completely regular** — iron constants,
one threshold, a halving chain with a remainder-absorbing last element. That is
ordinary integer arithmetic. The only genuinely LLM-shaped work in `/budget` is
parsing the free text (classifying a line as a one-off vs. a recurring cost from
an imprecise phrasing, reading a repayment plan out of a sentence like "pay it
off in full next month if there's room") and writing the qualitative advice
around the finished numbers.

Separately: the **financial cushion** ("подушка") was modelled as a regular
goal (`цель_подушка` Memory fact). Goals and the cushion have different
semantics — a goal is "save a fixed sum, buy one thing, done"; the cushion is a
buffer with no endpoint that should keep growing. Sharing the goals list mixed
the cushion in with the phone and the headphones, and no accumulation was tracked
for any of them.

## Decision

**Split responsibility: the LLM does judgement, Python does every number.**

### Deterministic distribution — `core/budget.py::compute_limits()`

A pure function, no LLM, unit-tested against exact amounts:

- `discretionary = distributable_pool − total_debt_payment`.
- Iron categories are fixed constants (transport, impulse), never cut while the
  pool covers them; below that they degrade proportionally.
- One threshold (`pool_after_iron ≥ 23000`) selects between the comfortable-month
  distribution (products at target, habits up to a ceiling, leftover down a fixed
  priority chain by halving, last element absorbs the rounding remainder so the
  category sum equals `discretionary` to the ruble) and the tight-month one
  (products/habits split the pool in half, priority chain at 0).
- The tight-month A/B fork calls `compute_limits()` **twice** with different
  `total_debt_payment` — the fork is structural, the arithmetic is the same
  function.
- Sonnet runs in **two phases**: phase 1 parses the dump into structured fields
  (no numbers it didn't read verbatim); Python computes the plan; phase 2 writes
  `summary` / `habit_strategy` / `adhd_survival_plan` / `creditor_script` around
  the already-final figures. The response schema no longer asks the model for
  `limits`, `impulse_budget`, or the variant objects.

Deadline-based "first burning debt" selection also moved to Python
(`parse_deadline` + `pick_debt_payment`) — a Russian-month string parser and a
sort, not a prompt instruction.

### Cushion as its own entity

- Own table (`cushion`: balance, target, planned contribution) plus a
  `cushion_transactions` log, not a Memory fact. Balance is **incremented, never
  overwritten**; each increment writes a log row in the same DB transaction.
- **Dynamic contribution.** In a comfortable month `compute_limits()` reserves a
  configurable share of income (`CUSHION_COMFORTABLE_RATE`, currently 20%) into
  the cushion **before** distributing limits, and returns that figure as an
  explicit `cushion_contribution` field — shown to the user, not silently
  subtracted. In a tight month it reserves nothing; whatever is left over goes to
  the cushion after the fact.
- The balance is credited **once per period, on the payday transition**
  (guarded so re-accepting the same plan mid-month cannot double-count):
  the accepted plan's `cushion_contribution` **plus** the real underspend
  (`total_saved` from the period review, when positive — works in both month
  types).
- Its own Mini App tab with the transaction history and an editable target,
  separate from the goals list.

## Alternatives considered

1. **Keep the whole algorithm in the prompt, lower temperature further / add
   "be deterministic" instructions.** Rejected: temperature is already 0; the
   drift is from compounded multi-step arithmetic, not sampling. Negative/meta
   instructions ("do not vary", "be exact") are the same class of rule the model
   was already ignoring. The firefighting commits are the empirical record.

2. **Have the LLM emit the numbers, then validate/repair them in Python
   (clamp to constraints, re-normalise).** Rejected: if Python has to know the
   full ruleset to check the output, Python can just produce the output. A
   repair layer is a second implementation of the same algorithm that only runs
   when the first one is wrong, and "wrong" is exactly what's hard to detect for
   a plausible-looking set of numbers.

3. **Single LLM call that both parses and narrates, Python overrides only the
   numbers afterward.** Rejected: the narration ("we're cutting habits to X")
   would cite figures the model computed, not the Python ones, and diverge from
   the plan shown. The two-phase split costs a second Sonnet call on an
   infrequent operation and is worth it for text that references the real numbers.

4. **Cushion stays a goal with an added "is_buffer" flag and accumulation
   tracking bolted onto the goals table.** Rejected: the goals table and its
   read/serialise paths assume "fixed target, then closed." A buffer with no
   endpoint, a running balance, and a transaction log is a different shape;
   overloading one table with a mode flag spreads `if is_buffer` through every
   goal code path. Separate table, separate semantics.

5. **Fixed monthly cushion contribution set by the user (the original
   `monthly_contribution` column).** Rejected after one iteration: a static
   number ignores the month. A good income month should save more; a month where
   the debt payment eats the discretionary pool should save nothing rather than
   forcing a transfer the person then has to claw back. The dynamic rule (share
   of income when comfortable, real underspend otherwise) tracks reality; the
   column was renamed to `planned_contribution` and is now written by the plan,
   not the user.

## Consequences

- (+) `/budget` is reproducible: the same dump yields the same plan. A
  recalculation now means the inputs changed.
- (+) The distribution rules are one readable function with unit tests on
  concrete numbers, instead of a prose spec the model re-interprets each call.
- (+) Adding or tuning a rule (the cushion rate, a category ceiling, the
  threshold) is a code change with a test, not a prompt edit with unpredictable
  blast radius.
- (+) The cushion has a real, auditable accumulation history and a contribution
  that adapts to the month.
- (−) Two Sonnet calls per `/budget` instead of one (parse + narrate). Acceptable
  — `/budget` is run roughly monthly.
- (−) The prompt-based path is gone, so any distribution behaviour that was only
  ever expressed in prose had to be restated as code; the tight-month "several
  categories at 0 is acceptable" outcome, for instance, is now an explicit
  branch rather than an emergent one.
- (−) A migration renames a column on a table users already have; the mechanism
  it fed (payday credit from a static figure) is replaced, not just moved.

## Verify against code

- `core/budget.py` — `compute_limits`, `_distribute_limits`,
  `CUSHION_COMFORTABLE_RATE`, `parse_deadline`, `pick_debt_payment`,
  `load_budget_data` (cushion read, `цель_подушка` skip)
- `nexus/handlers/finance.py` — `_apply_computed_limits`, `_limits_fields`,
  `BUDGET_SONNET_SYSTEM` / `_BUDGET_PARSE_PROMPT_LEGACY` (phase-1 schemas),
  `BUDGET_PHASE2_SYSTEM`, `_budget_phase2_narration`, `_format_plan`
  (cushion section), `_save_budget_plan`, `_send_payday_review`,
  `handle_cushion_command`
- `core/repos/cushion_table.py` / `core/repos/pg_cushion_repo.py` — table +
  incremental balance + transaction log
- `alembic/versions/b8c9d0e1f2a3_cushion.py`,
  `c9d0e1f2a3b4_cushion_transactions.py`,
  `d0e1f2a3b4c5_cushion_planned_contribution.py`
- `miniapp/backend/routes/finance.py` — `_view_cushion`;
  `miniapp/backend/routes/writes.py` — `POST /finance/cushion/target`
- `docs/specs/CUSHION.md`, `docs/specs/BUDGET.md`
