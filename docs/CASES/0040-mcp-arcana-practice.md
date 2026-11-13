# ADR-0040 — MCP may create a personal practice in Arcana (`POST /api/mcp/practice`)

- **Status:** Accepted (Nexus side); the consumer is `kai-mcp`, a separate private repository
- **Date:** 2026-11-13
- **Relates to:** ADR-0039 (the same pattern for Nexus tasks, and the overlap warning), ADR-0038 (read-only reminders of the practice)
- **Code conforms to:** `miniapp/backend/mcp_practice_auth.py`, `miniapp/backend/routes/mcp_practice.py`, `core/mcp_practice.py`,
  `arcana/repos/pg_works_repo.py` (`create_personal`, `_slot_fields`), `miniapp/backend/auth.py` (`current_user_id`),
  `miniapp/backend/mcp_read_auth.py` and `mcp_write_auth.py` (token separation), `core/config.py`, `.env.example`,
  `tests/test_mcp_practice.py`
- Update this ADR in the same PR that changes what MCP may write to Arcana.

## Context

Claude (through `kai-mcp`) should be able to put a **personal practice** (a spread, a ritual, a post, a purchase, a study item) on the
owner's list, on her explicit request. The practice lives in Arcana (table `works`), not in Nexus. Arcana holds client data, so the
write must be unable to touch it: no client, no session, no client note. The existing `POST /api/arcana/works` takes a `client_id`
without checking whom it belongs to, sends a chat message, and is reachable with a normal login, so it is not reused.

## Decision

One **new** route under a **separate** write token. The existing routes stay closed to it. Create only: no move, no edit, no delete
(a move is deliberately not offered, see "Not covered").

| Rule | Detail |
|---|---|
| Scope | Only `POST /api/mcp/practice`. A test walks **all** routes of the app with the practice token and fails if anything else answers differently from a request without the header; the read and write MCP tokens open nothing new and not this route; the practice token opens neither of their routes. |
| Token | `MCP_PRACTICE_TOKEN` (at least 32 characters) and `MCP_PRACTICE_OWNER_TG_ID` (must be in `ALLOWED_TELEGRAM_IDS`). Off when unset, short, or **equal to any other service token** (cats read, sitter lists, care write, care period, cats write, booking service, `MCP_READ_TOKEN`, `MCP_WRITE_TOKEN`); the read and write MCP gates check the practice token too. `hmac.compare_digest`; never logged. |
| Bearer mandatory | No fall-through to `initData` or the cookie. Failed attempts are counted per IP in the gate's own window: more than 10 in 60 s gives 429, also for the right token, until the window clears. |
| Identity | Always `MCP_PRACTICE_OWNER_TG_ID`; the work gets the owner's `user_id` (no `user_id`: 409, nothing written, because a work with an empty owner is visible to everybody in the booking busy view). |
| Body | Exactly five fields: `title`, `deadline_date`, `deadline_time` (optional), `category`, `prio`. `extra=forbid`: any other field, including `client_id`, `notes`, `repeat`, `reminder`, gives 422. |
| No client, by construction | The route calls `PgWorksRepo.create_personal`, which has **no** parameter for a client, a note, a repeat or a reminder; its INSERT names a fixed set of columns (`title`, `deadline`, `category`, `priority_id`, `status_id`, `user_id`, plus the slot below). A test reads the signature and the INSERT keys, another walks the route's syntax tree for forbidden arguments, another compares every column of the stored row. `client_id` is NULL, so the work-to-record linkage (`core/work_relation.py`) never matches it. |
| Client names in the title | The title is free text from a model, so it is also checked against the owner's client names (below). A match is 422 `client_name_in_title` **without saying which name**; nothing is written and the daily limit is not spent. Reading the client list fails: 503, nothing is written. |
| Title | 1..120 characters, no control characters or line breaks, no `<` or `>` (the title goes into Telegram messages with HTML parse mode). |
| Category, priority | Category from a fixed list of six (the same as the bot: spread, ritual, social, supplies, study, other), by name or full code, stored in its canonical form; default "🗂️ Прочее". Priority 🔴 🟡 ⚪ (default 🟡). Anything else: 422. |
| Dates | `deadline_date` within [yesterday; today + 2 years] of the owner's calendar. `deadline_time` is `HH:MM`; **`00:00` is refused**: for a work, midnight means "no time" everywhere (bot, dashboard, Zarya slot rule). |
| Limits | In process memory, sliding 24 h: 20 creations per day (429 with `Retry-After`; refused requests do not count; a restart resets the counter). |
| Alert | One message to the log group, topic `TG_LOG_THREAD_KAIMCP` (falling back to the Nexus topic): "Claude создал практику: <title>, срок …". Title only, HTML-escaped; no token, no user id. |
| Answer | `id, title, deadline, deadline_time, category, prio`, an echo of what was stored, plus `conflicts` (below). Errors carry `{code, message}`. |

### How a client name in the title is recognized

Each client's name is split into words; words shorter than three letters are ignored. The owner's own card ("Я", type self) is not
in the list, so a practice may name her. Comparison is case-insensitive and `ё` equals `е`. A title word matches when it **equals
one of the forms** of a name word as a whole word: the name itself, the usual case endings of masculine, feminine and soft-sign
names (for example `-а/-ы/-е/-у/-ой/-ей` after a feminine `-а`, `-а/-у/-ом/-е` after a consonant), and the possessives (`Машин`,
`Олегов`). A part of a word does not count (`верно` is not `Вера`). The check does not catch diminutives, typos, names under three
letters or a client named only by a description; it is a guard against slips, not a proof. The rest is the tool description's job
(it tells the model never to write client names) and the owner's own reading of the alert. A name that is also an ordinary word
(a client called like a common noun) will refuse titles that use the word; the owner rephrases.

## Side effects of a created practice (checked in the code, not assumed)

- **Chat.** Nothing is sent to the owner's chats: no "work created" message (the Mini App route sends one, this one does not), no
  reminder, no deadline ping, no change in the scheduler. Reminders of works are scheduled by the Arcana bot process (`arcana.bot`
  `arcana_reminder_flow`), while this API runs inside the Nexus bot process, where that scheduler is not initialized: a reminder
  written from here would fire only after the next restart of the Arcana bot. That is why the body has no `reminder`.
- **Zarya slot.** Read: `core/booking/busy.py`, `core/booking/agenda.py`, `core/booking/linkage.py`, `core/booking/ics.py`,
  `miniapp/backend/routes/booking.py` (feed, calendar, slots), `zarya/handlers.py` (every caller of `busy_intervals` and
  `day_agenda`). A work enters the owner's busy view **only through `works.scheduled_at`** (and booking-created works). The only
  writers of that column are `core/booking/linkage.py` (bookings) and `PgWorksRepo._slot_fields`, which sets `scheduled_at` and
  `duration_min = 60` only for a category containing "Ритуал" or "Расклад" **and** an explicit local time other than 00:00.
  So: a practice with a date and no time takes **no slot, not an hour and not the day**, in any of the six categories (unlike a
  Nexus task, whose all-day deadline blocks the whole day); a practice with a time in any category but spread and ritual takes none;
  a spread or ritual with a time takes one hour, exactly like one made by the bot. Tests run every category with and without time
  against the real `busy_intervals` and `day_agenda` and compare them before and after. A closed work gives its slot back.
- **Overlap warning (as in ADR-0039 D2).** If the practice took a slot, the final time is compared with bookings that hold a slot
  (`pending`, `confirmed`) and have not ended; `conflicts` is a list of `{date, starts, ends}` (empty if none, **`null` if the
  bookings could not be read**; the practice is written either way). A practice without a slot has `conflicts: []` and the bookings
  are not read. No name, id, contact or topic in the answer; the friend's name appears only in the alert to the closed topic. Bookings
  and the busy calculation are not changed.
- **Dashboard and lists.** The work is an ordinary open work: it shows in `/api/arcana/today`, `/api/arcana/works` and, through
  ADR-0038, in `arcana_reminders` (by deadline, since there is no reminder). It is never closed automatically (no client, so no record
  links to it); the owner closes it in the app.
- **Not touched:** no Google Calendar (the code has no sync with it), no message to a third person, no cost (no LLM call). The ICS
  feed for the owner's own calendar app lists works with a slot, so a timed spread or ritual appears there, with its title.

## Alternatives rejected

- **Reuse `POST /api/arcana/works` under a token:** it accepts any `client_id`, sends a chat message and schedules a reminder in a process
  where the scheduler is not running; a token for it would open far more than one action.
- **Add the practice categories to ADR-0039's task route:** that route writes Nexus tasks and refuses these categories on purpose; the
  practice must live in Arcana.
- **Allow `reminder` now:** it would be saved but not delivered until a restart. Left for a separate change that first gives the API
  process a way to reach the Arcana scheduler (or adds a sweep to the Arcana bot).
- **Refuse by `client_id IS NULL` instead of by construction:** no, the field is never accepted at all.

## Consequences

- No schema change, no migration. Rollback: empty `MCP_PRACTICE_TOKEN` in `.env` and recreate `nexus-bot` (the route answers 401);
  `kai-mcp` without `NEXUS_PRACTICE_TOKEN` does not register the tool.
- Enabling is an `.env` edit plus a recreate of `nexus-bot`; the same token goes to `kai-mcp` as `NEXUS_PRACTICE_TOKEN`.
- The token holder can add up to 20 practices per day, nothing more. A leaked token works from outside until rotated (blocking
  `/api/mcp/*` on the public host in Caddy is a cheap extra guard).
- A title can still hold a client's name in a form the matcher does not know. The tool description forbids it and the owner sees every
  creation in the alert topic.

## Not covered

- **Moving, editing, completing or deleting a practice through MCP.** A move was considered and refused: `client_id IS NULL` does not
  prove that a work has no client. Works created for friends' bookings (`core/booking/linkage.py`) have no client and carry the
  friend's name in the title, and older works can name a client in the title or the note without a client link. A move could be made
  safe only for works that this route created, which needs a provenance column on `works` (a migration); not done.
- The consumer (`kai-mcp`: the tool, its description that forbids client names and tells the model to ask the owner to rephrase on a
  422) lives in its own repository.
