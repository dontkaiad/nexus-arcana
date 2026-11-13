# Overnight report — 2026-11-13

Branch: `fix/overnight-2026-11-13` (from `main`; nothing was pushed to `main`, production was not touched).

## Summary

| # | Task | Result | Commit |
|---|------|--------|--------|
| 1 | Local Alembic, new pytest baseline | done, no repo files changed | — (no commit by design) |
| 2 | `03:00` for all-day deadlines in Mini App | fixed + tests | `fd9af87` |
| 3 | CSP + `eval` | investigation only, no changes | — |

## 1. Local Alembic and the new pytest baseline

**Which database.** `alembic/env.py` takes `DATABASE_URL` from the environment; with the repo `.env` it is
`postgresql+psycopg2://…@localhost:5432/nexus_arcana`. Checked that it is **not production**:
- port 5432 is listened on by Docker Desktop only, bound to `127.0.0.1`; container `nexus-arcana-db` (local compose stack, volume `ai_agents_pgdata`);
- no `ssh -L` / autossh tunnels running;
- production is a separate VPS stack (see `docs/ARCHITECTURE.md`, deploy workflow).

**Before.** `alembic_version` = `f4a8c2d6e913`, head = `d3f5a7c9e124` (4 pending: `care_periods_source` → `tasks_care_label` → `cat_health` → `memories_cat_block`).
I rendered the pending SQL offline (`alembic upgrade f4a8c2d6e913:head --sql`) and read it first: only `ADD COLUMN`, `CREATE TABLE`, `CREATE INDEX`; no drops or data changes.

**Backup.** `pg_dump -Fc` of the local DB (≈270 KB, 556 TOC entries, verified with `pg_restore -l`) saved in the session scratchpad, outside the repo.

**Complication.** `alembic upgrade head` failed on the first migration: `care_periods.source` already existed (DDL is transactional, so nothing was applied).
The first migration had been applied to the local DB by hand, without updating `alembic_version`. I compared the live column and CHECK constraint with the migration
(`TEXT`, default `'manual'`, `NOT NULL`, `CHECK (source IN ('manual','auto'))`) — identical — so I ran `alembic stamp a7c3e1b9d452`
(only rewrites `alembic_version`) and then `alembic upgrade head` for the remaining three. Local DB is now at `d3f5a7c9e124 (head)`; `memories.cat_block`,
`cat_weights`, `cat_vet_visits`, `cat_medications` exist. Why the column was there already is not known (hypothesis: a manual `ALTER` or an earlier interrupted run); not verified.

**Repo files changed:** none → no commit (as agreed).

### New pytest baseline (full `pytest tests/`, local DB at head)

- **4922 passed, 1 failed** (was 658 failed + 49 errors ≈ 701 ids, all caused by the missing schema).
- The single failing id:
  - `tests/test_care_period_auto.py::test_get_is_not_allowed_on_the_auto_route` — `GET /api/care/period/auto` returns `404`, the test expects `401` or `405`.
    Not a schema problem (routing/auth ordering). Not investigated further; left as is.
- After item 2 the suite is 4931 passed (+9 new tests), the same single failing id, no new failures (compared by test id).

## 2. `03:00` for deadlines without time (Mini App)

**Cause.** A deadline without time is stored as midnight UTC with `deadline_all_day = true`. The repo returns it as a full ISO string
(`2026-11-27T00:00:00+00:00`), so `extract_time` (`miniapp/backend/_helpers.py`) treated it as a timed value and printed `03:00` for UTC+3.

**Fix.** `extract_time(iso, tz_offset, all_day=False)`: `all_day=True` → `None`. All deadline call sites now pass the task's flag:

| File | Call |
|------|------|
| `routes/tasks.py` | task list serializer (`_serialize_pg_task`) |
| `routes/lists.py` | tasks attached to list groups |
| `routes/calendar.py` | calendar time (falls back to the reminder time when the deadline is all-day) |
| `routes/today.py` | `_task_summary` now carries `deadline_all_day`; used for the scheduled/no-time split |
| `routes/writes.py` | task edit (see behaviour change below) |

Not touched on purpose: reminder calls (`routes/tasks.py`, `routes/today.py`) — reminders always have a time;
`routes/_arcana_common.py` (Arcana sessions come from page-like data with no all-day flag).
`routes/mcp_tasks.py` already handled the flag.

**Behaviour change to be aware of.** `routes/writes.py` (task edit): when only the date of an all-day deadline is edited, the old code
inherited the phantom `03:00` as the "old time"; now there is no old time, so the existing default `09:00` applies.
The edited deadline therefore becomes a timed deadline at 09:00 (previously 03:00) rather than staying all-day. I did not change that semantics.

**Tests.** `tests/test_extract_time_all_day.py` (9 tests): helper (flag, regression of the `03:00` cause, timed deadline unchanged, empty/date-only),
tasks list serializer (all-day vs timed, reminder time still returned), today summary.

**Not verified / possible follow-up.** For negative UTC offsets `to_local_date` of an all-day deadline (midnight UTC) may land on the previous day.
Owner timezone is positive, so it does not show up; hypothesis only.

## 3. CSP and `eval` — investigation only (no changes)

**Where the CSP header comes from.** Nowhere on our side:
- `curl -sI https://core.heylark.dev/` (and `/assets/*.js`, `/nexus`, `/api/*`): **no** `Content-Security-Policy` / `-Report-Only` header; response is `server: uvicorn`, `via: 1.1 Caddy`, no Cloudflare headers.
- `heylark.dev` and `booking.heylark.dev`: no CSP header either.
- Live `index.html` has no `<meta http-equiv="Content-Security-Policy">`.
- Repo: no Caddyfile/nginx/compose CSP; backend sets only `Cache-Control`. Caddy runs on the server (`docs/ARCHITECTURE.md`), its config is not in the repo.

**Which code uses `eval` / `new Function`.** None found:
- Production bundles currently served (`index-*.js`, `react-vendor-*.js`, `icons-*.js`) and the external `https://telegram.org/js/telegram-web-app.js`: 0 matches for `eval(`, `new Function`, `Function(`, string `setTimeout`.
- Local `dist/` (three bundles): same, 0 matches.
- Dependencies of the Mini App: React 18, DOMPurify, lucide-react, phosphor-icons — none use `eval` in production builds.

**Conclusion.**
- The `eval` being blocked is **not** coming from our code, and our server does not send a CSP, so the message must come from the client environment:
  a browser extension / DevTools hook, or the host (Telegram client WebView / an embedding page) applying its own policy. The blocked script URL printed in the console message would identify it; I cannot see that message.
- It is safe to ignore **for the app**: the application never calls `eval`, so blocking it cannot break app behaviour (the app works with it blocked).
  Do not "fix" it by loosening CSP (there is none to loosen) or by adding `unsafe-eval`.
- Not verified: behaviour inside the real Telegram clients (cannot be tested from here). If it ever matters, the first thing to capture is the console line with the blocked source file.

## Not done / left for later

- `tests/test_care_period_auto.py::test_get_is_not_allowed_on_the_auto_route` (404 instead of 401/405) — not investigated.
- Origin of the manually applied `care_periods.source` column in the local DB — unknown.
- Production was not touched; nothing here was deployed (branch only).
