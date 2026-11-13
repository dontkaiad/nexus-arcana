# ADR-0038 — Read-only service token for tasks and practice reminders (`GET /api/tasks`, `GET /api/arcana/reminders`) for the personal MCP server

- **Status:** Accepted (Nexus side); the consumer is `kai-mcp`, a separate private repository (see "Not covered")
- **Date:** 2026-11-13
- **Relates to:** ADR-0033 (the same pattern for cat cards), ADR-0012 (access model)
- **Code conforms to:** `miniapp/backend/mcp_read_auth.py`, `miniapp/backend/auth.py` (`current_user_id`),
  `miniapp/backend/routes/tasks.py`, `miniapp/backend/routes/arcana_today.py` (`get_arcana_reminders`), `core/config.py`, `.env.example`, `tests/test_mcp_read_token.py`
- Update this ADR in the same PR that changes who may read tasks or what they contain.

## Context

`kai-mcp` (an MCP server for claude.ai, running as another container on the same Docker network) must show the owner
her open tasks. It has no Telegram `initData` and no browser cookie, so it cannot use the Mini App login. Tasks are
personal: free text (`title`, `note`) can name other people (a friend from a booking, a birthday, a cat).

## Decision

A static bearer token, read-only, valid for exactly two routes.

| Rule | Detail |
|---|---|
| Scope | Only `GET /api/tasks` (`filter` = active, overdue, today, done, all; `limit`) and `GET /api/arcana/reminders` (`days` 0..31, see below). Every other route, every write, admin, finance, Arcana and cats stay closed. A test walks **all** routes of the app with a valid token and fails if anything but these two GETs answers differently from a request without the header; a mutation check (the gate on a third route) makes it fail. |
| Identity | The request runs as `MCP_READ_OWNER_TG_ID`, which must be in `ALLOWED_TELEGRAM_IDS`; the usual per-owner scoping then applies. |
| Secret | `MCP_READ_TOKEN` from the server `.env` (at least 32 characters). Off when unset, short, equal to any other service token (cats read, sitter lists, care write, care period, cats write, booking), or the owner is not configured or not allowed: every bearer request is then 401. |
| Comparison | `hmac.compare_digest` on bytes; an empty token never matches an empty setting. |
| Bearer only | If `Authorization` has the **Bearer** scheme it must be valid, otherwise 401. **Any other scheme** (for example `tma <initData>`) is not ours: the gate does nothing and the normal login (`initData`, `hl_session`) decides. This differs on purpose from ADR-0033, where any scheme except Bearer is 401. Without the header nothing changes. |
| Brute force | Failed bearer attempts are counted per client IP in the gate's **own** window (as for the other service gates): more than 10 in 60 s gives 429, including for the right token, until the window clears. Other schemes are not counted. |
| Privacy | A request that came through the gate does not receive tasks of the categories "Практика" and "Расходники" (compared by name without the emoji). The filter runs before the status filters, sorting, `limit` and `total`, so the counts do not betray hidden tasks. Normal login is unchanged. |
| Responses | `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. The token is never logged; failures are logged with the IP only. |

## `GET /api/arcana/reminders` (added 2026-11-13)

Open works of the practice that ring today or in the next `days` days (0..31, default 7) by the owner's **local calendar**, plus the overdue ones.
"Overdue" is the same rule as the works list in the Mini App (`/api/arcana/works`): the local **date** of the deadline is before today (a deadline
this morning is not overdue until tomorrow). `done` and `archived` are never returned (the repo's `list_open`, one source for terminal statuses).
The time of an item is the reminder, or the deadline when there is no reminder; works with neither are skipped. Sessions are not included
(they hold a date without a time, a record of a past reading, not an appointment).

| Rule | Detail |
|---|---|
| Shape | `{"days": N, "items": [...]}`; every item has **exactly five keys**: `at` (ISO time in the owner's zone), `kind` (always `work`), `category`, `priority`, `overdue`. No id, title, note, client, price. |
| White lists | `category` outside the six work categories becomes "🗂️ Прочее"; `priority` outside "Срочно", "Важно", "Можно потом" becomes `null`. A foreign value never leaves as it is. |
| Owner scoping | Without a `user_id` for the token's owner the answer is empty: `list_open("")` does **not** filter by owner and would return the works of every user. |
| Privacy test | A work with a client, a title, a note, a price-like text and a foreign category is created in the test database; the raw body is searched for every one of those strings (client name, client contact, request and notes, title, note, price, category) and for the ids, and must contain none of them; the keys are checked against the five. |

The works table has no price column (a price lives with sessions and finance), so a price never reaches this route by construction.

## Alternatives rejected

- **Reuse the cats token or the owner's session:** one leaked secret would open several things; a container cannot produce `initData`.
- **A dedicated `/api/mcp/tasks` router:** duplicates the response shape and the owner scoping; the gate on the existing GET plus a test that pins the scope is smaller.
- **Share one limiter between all service gates:** a noisy client of one gate could lock out another; the repo keeps one window per gate.
- **Fail with 401 on any non-Bearer scheme (as ADR-0033 does):** a proxy or client that sends another scheme for its own reasons would lose normal login on `/api/tasks`.

## Consequences

- No schema change, no migration. Rollback: empty `MCP_READ_TOKEN` in `.env` and recreate `nexus-bot` (bearer requests become 401, Mini App users are unaffected); `kai-mcp` without `NEXUS_MCP_TOKEN` serves only job-hunter.
- Enabling is an `.env` edit on the server followed by a recreate of `nexus-bot`; the same token goes to `kai-mcp` as `NEXUS_MCP_TOKEN`. Rotation: change both, recreate both.
- The token holder reads every task of the owner except the hidden categories. That is the intent.
- The public proxy also serves this route: a leaked token works from outside until rotated (256 bits, read-only, rate-limited).
- **`note` does not go to MCP (decided on the `kai-mcp` side).** `/api/tasks` still returns `note` (the Mini App needs it), but `kai-mcp` drops it before the
  answer reaches Claude: Claude gets `id`, `title`, `cat` (as a string), `prio`, `status`, the dates and times, `repeat`, `closed_at`. Why: the note is free text
  and is where other people's names land (bookings write "с <имя> · бронь #…", birthdays and cat visits add names too), while the owner's question
  "what is on my list" does not need it. The cut is on the consumer's side, so a leaked token still reads `note` from this API; the filter below and
  this cut are two separate guards. A test in `kai-mcp` fails if `note` ever appears in the `nexus_tasks` answer, even when the backend sends it.
  `title` is still free text and is not filtered.
- The category filter is a guard, not proof of privacy: today these two names are finance and list categories and are not stored for tasks (`task_category` has no such row, a task saved with such a category falls back to "Прочее"). Private names in a free-text `title` or `note` are **not** filtered.
- A shared in-memory limiter is per process; a second replica would count separately (single container today).

## Not covered

The consumer (`kai-mcp`: client, the cut-down task view without `note`, the `arcana_reminders` tool switched on only by `ARCANA_ENABLED=1` and re-filtered by its own white list, rendering) lives in its own repository.
Live verification against production is a post-deploy read-only smoke, not a CI test.
