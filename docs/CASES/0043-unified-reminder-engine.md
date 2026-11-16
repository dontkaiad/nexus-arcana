# ADR-0043 — One reminder/deadline engine for Nexus tasks and Arcana works

- **Status:** Accepted
- **Date:** 2026-11-16
- **Relates to:** ADR-0023 (recurring works), AUDIT_works_vs_tasks_parity (debt item 4), issues #69, #206, #210, #212, #306
- **Code conforms to:** `core/reminder_scheduler.py`, `arcana/reminder_adapter.py`, `nexus/handlers/tasks.py` (`_flow`),
  `arcana/bot.py`, `tests/test_reminder_parity.py`

## Decision

1. **One engine.** `core/reminder_scheduler.py` owns every mechanic: APScheduler jobs, "just passed" window, status re-check before sending
   (Done/Archived stay silent), one-shot reminder cleared after firing, `duration_min` shifting the deadline ping, `arm()` (reminder, else
   deadline ping, #69) and `restore()` passes 1-4 (future / missed / revive recurring / deadline without reminder) with the periodic mode (#210).
2. **What differs is an adapter**, not a copy: repository, callback prefix and keyboards, wording, owner grouping (#202), date representation
   (a task's all-day deadline is a UTC date plus a flag; a work's is local midnight), and hooks (task: "In progress" on fire, care mute,
   `save_task_reminder`; work: none). Nexus keeps `_schedule_reminder`, `_schedule_deadline_check`, `restore_reminders_on_startup` as thin wrappers
   because ~15 call sites and tests use those names.
3. **Arcana gains** the deadline ping on create/edit, "⏰ Пропущено" instead of silent loss, deadline-without-reminder after restart, a 90 s
   periodic re-arm, and the status re-check. The default reminder "deadline minus one day" is dropped when it is already past (as in Nexus
   `_default_reminder`); otherwise the re-arm would report it as missed a minute after creation.
4. **Mini App and MCP do not call the scheduler to make it work.** The Mini App backend runs inside the Nexus process, where the Arcana flow is
   never initialised; the Arcana re-arm picks up whatever they write to PG within 90 s (passes 1 and 4). `mcp_practice` keeps its
   by-construction guard (no scheduler or notify names in the route, ADR-0040) and is covered by the same sweep.
5. **Mini App display.** `deadline_label` is the local wall time (it was the UTC string). `set_deadline` takes an optional `tz_offset` so a
   postponed work keeps "local midnight" instead of UTC midnight (which showed 03:00 and pinged at 03:00).
6. **Parity is a test.** `tests/test_reminder_parity.py` runs one set of scenarios on both adapters and fails on any difference in jobs, sent
   messages or cleared reminders. Keyboards differ on purpose and are asserted separately.

## Trade-offs

- A work created in the Mini App is armed up to 90 s later, not instantly. The alternative (an RPC between the two processes) adds a
  moving part for a delay nobody can notice.
- The adapter is a class with ~15 hooks. A smaller protocol would hide the passes' real needs; the hooks mirror the SQL queries each repo already has.

## Rollback

`git revert` the commit. No schema change; jobs are in memory and are rebuilt from PG on the next start.
