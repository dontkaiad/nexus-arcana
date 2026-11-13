# ADR-0039 — MCP may create tasks and move their deadline (`POST /api/mcp/tasks`, `POST /api/mcp/tasks/{id}/reschedule`)

- **Status:** Accepted (Nexus side); the consumer is `kai-mcp`, a separate private repository
- **Date:** 2026-11-13
- **Relates to:** ADR-0038 (read-only gate for the same server), ADR-0033 (the pattern)
- **Code conforms to:** `miniapp/backend/mcp_write_auth.py`, `miniapp/backend/routes/mcp_tasks.py`, `core/mcp_tasks.py`,
  `miniapp/backend/auth.py` (`current_user_id`), `miniapp/backend/mcp_read_auth.py` (token separation), `core/config.py`, `.env.example`,
  `tests/test_mcp_write_token.py`
- Update this ADR in the same PR that changes what MCP may write.

## Context

Claude (through `kai-mcp`) should be able to do two things for the owner, on her explicit request: add a task and move the deadline of an
existing one. Nothing else: no rename, no note, no status change, no delete. The existing Mini App routes (`POST /api/tasks`, `/edit`,
`/postpone`, `/done`, `/cancel`) do more than that and send chat messages, so they are not reused.

## Decision

Two **new** routes under a **separate** write token. The existing routes stay closed to it.

| Rule | Detail |
|---|---|
| Scope | Only `POST /api/mcp/tasks` and `POST /api/mcp/tasks/{id}/reschedule`. A test walks **all** routes of the app with the write token and fails if anything else answers differently from a request without the header; the read token opens neither POST; the write token opens neither read GET; mutation checks (the gate on a third route, a read token accepted by the write gate) make it fail. |
| Token | `MCP_WRITE_TOKEN` (at least 32 characters) and `MCP_WRITE_OWNER_TG_ID` (must be in `ALLOWED_TELEGRAM_IDS`). Off when unset, short, or **equal to any other service token, including `MCP_READ_TOKEN`** (the read gate checks the write token too). `hmac.compare_digest`; never logged. |
| Bearer mandatory | Unlike the read gates there is no fall-through to `initData` or the cookie: without a valid Bearer the routes answer 401. Failed attempts are counted per IP in the gate's own window: more than 10 in 60 s gives 429, also for the right token, until the window clears. |
| Identity | Always `MCP_WRITE_OWNER_TG_ID`; the task is written with the owner's `user_id` (no `user_id`: 409, nothing written, because tasks with an empty owner are visible to everybody in the booking busy view). |
| Create | `title` 1..200 characters (no control characters or line breaks, and no `<` or `>`: the title goes into Telegram messages with HTML parse mode and comes from a model); `deadline_date` (required) and optional `deadline_time`, `reminder` (`YYYY-MM-DDTHH:MM`, owner's local time), `prio` (🔴 🟡 ⚪, default 🟡), `category` (default "Прочее"; "Практика" and "Расходники" are refused even if the dictionary ever gets such a row). Unknown fields: 422. `for_sitter` false, no repeat, no note, no cat. A deadline without a time is an all-day deadline, as everywhere in the app. |
| Move | Only `deadline_date`, optional `deadline_time` (omitted: the old time of day stays; an all-day deadline stays all-day) and optional `reminder` (omitted: the old reminder stays, but must not end up later than the new deadline, else 422). The task must belong to the owner (someone else's and missing look the same: 404), be active, in progress or overdue, **not** done/archived, not `for_sitter`, not recurring (409 with a plain reason), and also **not linked to a booking or to a cat** (409): moving such a task from the bot moves the booking and writes to the friend through Zarya, or desyncs the cat record, and this route does neither. Only the columns deadline, deadline_all_day and reminder are written (a test compares every other column before and after). The answer has the old and the new values. |
| Dates | Deadline within [yesterday; today + 2 years] of the owner's calendar. A new reminder must not be later than the deadline and must not be in the past. |
| Limits | In process memory, sliding 24 h: 20 creations and 30 moves per day (429 with `Retry-After`; refused requests do not count; a restart resets the counters). |
| Alerts | One message per action to the log group, same bot and mechanism as the other alerts (`notify_log_group`), topic `TG_LOG_THREAD_KAIMCP`, falling back to the Nexus topic: "Claude создал задачу: <title>, срок …" and "Claude перенёс задачу #id <title>: было …, стало …". Title only, HTML-escaped; no token, no user id, no note. |
| Answers | Create: `id, title, deadline, deadline_time, reminder, prio, category`. Move: `id, title, old, new`. Errors carry `{code, message}`. |

## Side effects of a created or moved task (checked in the code, not assumed)

- **Reminder in chat.** A task with a `reminder` is scheduled in the bot's APScheduler exactly like a bot-created one (`_schedule_reminder`). At the time the owner gets "🔔 Напоминание: <title>" with done / not done buttons, the task becomes "In progress", and a one-shot reminder is cleared. After a bot restart the restore pass sends it to **all** Telegram accounts of the owner's `user_id` (both of Kai's accounts), a live-scheduled one only to the owner id of the token. A reminder in the past is skipped; within two minutes it is sent at once (hence: new reminders must be in the future).
- **Deadline ping.** Without a reminder a deadline ping "⏰ Дедлайн: <title>, Сделал?" is scheduled (all-day deadlines at 09:00, as in the bot).
- **Booking calendar.** Every open task with a deadline occupies the owner's time in the Zarya booking view (`core/booking/busy.py`): an all-day deadline blocks the **whole local day**, a timed one one hour. A task made through MCP therefore changes which slots friends can book. Titles are hidden from non-admin viewers.
- **Nothing for anybody else:** no Google Calendar (tasks are not synced there; the ICS feed holds bookings only), no message to a third person (that is why booking-linked tasks are refused), no sitter schedule (`for_sitter` stays false), no cost (no LLM call).
- The chat confirmation that the Mini App sends on create ("➕ Создала задачу") is **not** sent: the log-group alert replaces it.

## Warning about an overlap with a friend's booking (D2, added 2026-11-13)

A task made or moved through MCP occupies the owner's time in the Zarya booking view (see the side effects above). Zarya's busy calculation
and the bookings themselves are **not** changed (variant A); instead the answer **warns**.

| Rule | Detail |
|---|---|
| When | After a successful create or reschedule, the final time of the task is compared with the existing bookings. The task is created or moved **anyway**; nothing is blocked. |
| Length of the task | The same rule as `core/booking/busy.py`, taken from it (`DEFAULT_BUSY_MINUTES`, `_all_day_span`): a task with a time takes its `duration_min`, or 60 minutes when it has none (a task created from MCP has none; a moved one keeps its own); a task without a time (an all-day deadline) takes the **whole local day** in the display zone of the booking surface (hard-coded MSK, as in `busy.py`). |
| Length of a booking | Its real `start_at` to `end_at` (no default; the booking holds what the friend asked for). |
| Which bookings | Only those that hold a slot (`BLOCKING_BOOKING_STATUSES`: `pending`, `confirmed`) and have not ended yet. Declined, cancelled by either side, expired and completed ones do not count; a past booking does not count; a booking of another owner does not count (a legacy booking with an empty owner does, exactly as in `busy.py`). |
| Overlap | Strict, as in `busy.py` (`start < end` and `end > start`): a booking that ends when the task starts (or starts when it ends) is **not** an overlap; one minute of common time is. |
| Answer | `conflicts`: a list of `{date, starts, ends}` (the owner's local time), sorted by start; an empty list when nothing overlaps; **`null` when the bookings could not be read** (the task is still written). **No name, no booking id, no contact, no topic.** |
| Alert | One extra message to the same topic: "⚠️ Задача #id <title> пересекается с бронью <имя> <дата начало-конец>, перенеси" (several bookings in one message). **The friend's name appears only here** (the closed topic); title and name are HTML-escaped; the contact and the topic of the booking are never read into a message. |
| Read only | Bookings are read through the existing `core.booking.repo.list_bookings`; no write to the booking tables, no new route, nothing new for the write token (the all-routes test still shows exactly the two POSTs; a test compares the whole booking table before and after). |

`kai-mcp` passes `conflicts` on through a white list (`date`, `starts`, `ends`) and its tool descriptions tell the model to say it to Kai in plain words and offer to move the task.

## Alternatives rejected

- **Reuse `POST /api/tasks` and `/postpone` under a token:** they also edit, cancel and close, and send chat messages; a token for them would open much more than two actions.
- **Allow moving booking- or cat-linked tasks:** the bot moves the booking and writes to the friend; doing half of that silently is worse than refusing.
- **Fall through to `initData` on the write routes:** the routes exist only for the server; a browser session has no reason to reach them.
- **Per-IP daily limits:** there is one owner and one server; a global counter is what protects the list.

## Consequences

- No schema change, no migration. Rollback: empty `MCP_WRITE_TOKEN` in `.env` and recreate `nexus-bot` (the routes answer 401); `kai-mcp` without `NEXUS_WRITE_TOKEN` does not register the two tools.
- Enabling is an `.env` edit plus a recreate of `nexus-bot`; the same token goes to `kai-mcp` as `NEXUS_WRITE_TOKEN`; add `TG_LOG_THREAD_KAIMCP` for the alert topic.
- The token holder can add up to 20 tasks and move up to 30 per day, nothing more. A leaked token works from outside until rotated (the public proxy serves these paths; blocking `/api/mcp/*` on the public host in Caddy is a cheap extra guard).
- A title is still free text typed by a model: it is shown to the owner in her own chat. The `< >` ban and escaping in alerts keep markup out; the content itself is the owner's to judge.

## Not covered

The consumer (`kai-mcp`: the tools, their descriptions that tell the model to act only on an explicit request and to name the old and new value before a move) lives in its own repository.
Moving booking-, cat- and recurring tasks, editing, completing and deleting are out of scope on purpose.
