# Case 0041 — Editing the last task, deterministic dates, the `question` intent

- **Status:** Accepted
- **Date:** 2026-11-13
- **Relates to:** Case 0040 (state in `data/`), `docs/specs/TASKS.md`
- **Code:** `core/date_extract.py` (`parse_when`, `merge_when`, `strip_when_tokens`), `nexus/handlers/tasks.py` (`_LAST_TASK_TTL`, `_LAST_TASK_MAX_AGE`,
  `_is_clarification_not_new_task`, `_override_when_from_text`, `handle_task_question`), `core/reply_update.py` (`merge_task_when_updates`),
  `core/classifier.py` (`_TASK_QUESTION_RE`), `nexus/nexus_bot.py` (`_is_reply_to_task_card`),
  `tests/test_task_edit_context.py`, `tests/test_task_card_reply_persist.py`

## Problem

A follow-up like "reminder on the 20th at 15" right after creating a task produced a **new** task with the date in the next month. Three causes:
the "last task" context expired or was ignored when the message was a reply to a card; the LLM parser turned a bare day of the month into the next month;
a question about tasks ("is there a task about X?") was classified as a new task.

## Decision

- **Reply wins over `last_task`.** A reply to a mapped task card always edits that card's task (`_is_reply_to_task_card`); `last_task` is only the fallback for
  a message that is not a reply.
- **Window:** `last_task` lives 10 minutes, extended only by a successful edit (sliding), with an absolute ceiling of 30 minutes since creation.
  A message that clarifies (date, time, priority) is an edit; a message that looks like a new task stays new (`_is_clarification_not_new_task`).
- **Dates are parsed by code, not by the model.** `parse_when` understands explicit forms only (day of month, exact month names, "tomorrow", times);
  a misspelled month is **not** guessed. A day of the month not earlier than today stays in the current month. Dates without a time are all-day.
- **Override on create and on edit.** `_override_when_from_text` replaces the model's reminder/deadline with what the text says explicitly; what the text
  does not say (for example the time) is kept from the model. An edit takes the **date from the existing task** when only a time is given
  (`merge_task_when_updates`).
- **Intent `question`.** A question about tasks is matched by `_TASK_QUESTION_RE` before the "new task" path and answered by a search, nothing is created.

## Consequences

- Behaviour for explicit dates is deterministic and covered by tests with a frozen clock; ambiguous input still goes to the model.
- A stale context (after the 30-minute ceiling) never edits an old task by accident.
- Lookup of the card after a restart depends on Case 0040.
