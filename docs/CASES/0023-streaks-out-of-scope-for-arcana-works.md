# ADR-0023 — Streaks are not ported to Arcana works; repeat is tracked separately

**Status:** Accepted
**Date:** 2026-09-05
**Commit:** this one
**Relates to:** ADR-0002 (Nexus/Arcana coupling — «сёстры» share primitives *where the
domain matches*), `docs/CASES/AUDIT_works_vs_tasks_parity.md` (the parity audit that
raised the question)
**Code conforms to:** no code change — `core/task_streaks.py`, `nexus/handlers/tasks.py`
(`_handle_recurring_deadline_done`), `nexus/handlers/streaks.py`,
`arcana/handlers/works.py` left as-is

---

## Context

The parity audit (`AUDIT_works_vs_tasks_parity.md`) checked the spec claim
«Работы = Задачи Nexus с полями практики» against the code. Two mechanisms present
for Nexus tasks are absent for Arcana works: **streaks** and **repeat**. This ADR
covers the streak decision; repeat is explicitly out of scope (see below).

### How streaks actually work in Nexus

There are two separate streak systems:

1. **Global per-user streak** — `nexus/handlers/streaks.py`
   (`update_streak`, `get_streak`, `request_rest_day`, `is_rest_day_available`).
   Counts *any* completed task toward one per-user counter, and owns the
   "rest day" feature. Fired from `_update_streak_line` on every task completion.

2. **Per-task streak** — `core/task_streaks.py` (`update_task_streak`,
   `reset_broken_streaks`). One row per `(user_id, task_id)`, tracks a single
   recurring task's own run: «Зарядка», «Пить воду».

The per-task system is the one the request asked to wire into works. Its
continuation logic is **built on `repeat_kind`**: `_period_days(repeat_kind)`
(`core/task_streaks.py:55-66`) decides the gap between two successful runs
(«неделя»→7, «месяц»→30, else→1). Without a real `repeat_kind` the function has
no meaning.

The only place the bot calls `update_task_streak` is
`nexus/handlers/tasks.py:1425-1436`, inside `_handle_recurring_deadline_done` —
i.e. **only when a *recurring* task is completed**, passing the task's real
`repeat` field as `repeat_kind`. A one-off task completion (`task_complete`,
`nexus/handlers/tasks.py:2265-2314`) never touches `core/task_streaks.py`; it
only bumps the global streak.

### Why works don't fit

Arcana works are **one-off client records** — "финансовый ритуал для Маши",
"расклад на работу Игоря". They are done once, not repeated on a cadence. There
is no "recurring work completed" event to mirror, because works have no repeat
concept at all (no `repeat_id`/`repeat_time` column — `arcana/repos/works_tables.py`).

Kai confirmed: **streaks are not a feature Arcana needs.** The practice CRM is not
a habit tracker; a "3-day streak of doing rituals for clients" is not a metric
that means anything for this domain.

## Decision

**Streaks (`core/task_streaks.py` and `nexus/handlers/streaks.py`) are not ported
to Arcana works.** `arcana/handlers/works.py` / `mark_work_done` do not call any
streak function. This is a permanent domain decision, not a deferred task — it
does not belong in the backlog as "todo".

The parity audit row for «Стрики» is marked **WON'T DO (design decision)** with a
pointer to this ADR.

## Alternatives considered

1. **Wire `update_task_streak` into `mark_work_done` with a hard-coded
   `repeat_kind` (e.g. `"Ежедневно"`).** Rejected. `_period_days` would treat
   every work as a daily habit; since a given work is completed exactly once,
   `current_streak` collapses to 1 on essentially every call and
   `reset_broken_streaks` zeroes it on the next Mini App visit. The result is a
   table full of `current=1 / best=1` rows that look like data but carry no
   signal. Forcing a metric the domain doesn't have produces garbage, not parity.

2. **Wire only the global streak (`nexus/handlers/streaks.py::update_streak`)
   into work completion.** Rejected. Same domain-fit problem one level up: it
   would fold Arcana client work into Kai's *personal* daily-consistency streak
   and rest-day budget, conflating "I kept my own habits today" with "I did paid
   work for a client". These are different things and Kai tracks them separately.

3. **Generalise `core/task_streaks.py` first (add an `entity_type` column,
   make the period configurable per entity).** Rejected as premature. The schema
   is already generic enough (`task_id` is plain `TEXT`, no FK to `tasks`); the
   blocker is not the schema, it's that the *domain* has no habit semantics. No
   amount of generalisation makes a one-off record into a streak.

## Consequences

- `core/task_streaks.py` stays Nexus-only by usage. Its docstring already says
  "per repeating task" — no change needed.
- If Arcana ever grows **recurring** rituals/works (see repeat, below), the
  streak question can be *reopened* — but even then it is a separate decision,
  because "this ritual repeats monthly" does not imply "show me a streak for it".
- No test change: there is nothing to assert about a call that is deliberately
  absent. The audit doc + this ADR are the record.

## Out of scope — repeat

**Repeat *is* wanted** for genuinely recurring practice work (a monthly retainer
ritual, a weekly social-media batch). Repeat is a scheduling/recurrence feature;
it is **independent of streaks**. Porting repeat does not pull streaks in with it
— the `_handle_recurring_deadline_done` path in Nexus happens to call both, but
for Arcana the recurrence half is in scope and the streak half is not (this ADR).

**Status: repeat implemented** (migration `ab12cd34ef56`, `core/recurrence.py`,
`arcana/handlers/work_reminder_kb.py::_handle_recurring_work_done`). It calls the
shared recurrence math and reminder scheduler and, per this ADR, **does not**
call `core/task_streaks.py`. See the updated «Repeat + repeat_time» row in the
audit doc.
