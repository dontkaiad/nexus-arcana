# ADR-0016 — Single-writer location: `core/location.py` owns tz and city

- **Status:** Accepted
- **Date:** 2026-06-25
- **Relates to:** ADR-0002 (core/domain split), ADR-0005 (memory store)
- **Code conforms to:** `core/location.py`, `nexus/handlers/tasks.py` —
  `_update_user_tz`, `core/shared_handlers.py` — `/tz` dispatch,
  `miniapp/backend/routes/weather.py` — `POST /weather/city`,
  `core/time_manager.py`
- Update this ADR in the same PR that changes the writer, the `CITY_TZ` lookup
  table, or the storage keys (`tz_{tg_id}` / `city_{tg_id}`).

## Context

Timezone offset and city are consumed by many parts of the system: task
reminders (APScheduler jobs keyed to local time), deadline formatting in the bot
and Mini App, the weather route in the Mini App backend, and the LLM classifier
that builds a "today" anchor for date parsing. The offset changes legitimately —
Kai travels, sometimes long-term — and must stay consistent across all consumers:
a stale offset means reminders fire at the wrong local time and LLM date anchors
use the wrong "today".

Before issue #170, tz and city were stored independently by two different callers:
the bot's `/tz` handler wrote `tz_{tg_id}` in Memory, and the Mini App's
`POST /weather/city` endpoint wrote `city_{tg_id}` directly — bypassing the bot's
tz logic. After a location change via the Mini App, the weather widget updated but
the task scheduler continued firing on the old offset. The two keys drifted silently
with no error and no observable failure until reminders fired at the wrong time.

## Decision

**`core/location.py` is the single writer and single reader for both `tz_{tg_id}`
and `city_{tg_id}` in Memory (PgMemoryRepo). All call sites — bot and Mini App —
go through it.**

**`set_user_location(tg_id, *, offset, city, user_notion_id)`** — the only writer.
Writes both keys in a single logical operation (offset → `tz_{tg_id}`, city →
`city_{tg_id}`) and immediately updates the in-process TTL cache so subsequent
reads in the same process see the new offset without waiting for the 60-second TTL.
If `offset=None` (city not in `CITY_TZ`), `tz_{tg_id}` is left unchanged —
city is stored for the weather widget, tz is not corrupted with a null.

**`get_user_tz(tg_id) → int`** — the only reader. TTL-cached (60 s) read from
`tz_{tg_id}` in PgMemoryRepo; default 3 (Moscow) if nothing stored or parsing
fails.

**`resolve_offset(text) → (offset, matched_city)`** — pure parsing helper, no I/O.
Substring-match against `CITY_TZ` (~80 entries: RF, CIS, Turkey, Europe, Asia, USA)
then `UTC±X` regex, then `(None, None)` if unrecognised. The caller decides the
fallback (bot: Haiku; Mini App: leave tz unchanged, log the miss).

**`core/time_manager.py`** shares the in-process cache (`_tz_offsets`) directly for
synchronous use in `get_user_now` — read-only view of the cache populated by
`set_user_location` and `get_user_tz`.

**Two call sites reach `set_user_location`:**
1. `nexus/handlers/tasks._update_user_tz` — invoked on `set_tz` intent (classifier)
   and on the `/tz` slash command (shared handler). Haiku fallback for unrecognised
   city text.
2. `miniapp/backend/routes/weather.set_weather_city` (`POST /weather/city`) —
   invoked when the user sets their city in the Mini App weather widget. Calls
   `resolve_offset` first; if unrecognised, passes `offset=None` so only `city_` is
   written and `tz_` is left intact.

No other caller writes `tz_{tg_id}` or `city_{tg_id}`.

## Alternatives considered

- **Each consumer reads and writes its own tz key** — rejected: the pre-#170 state.
  Led to `tz_{tg_id}` (bot) and `city_{tg_id}` (Mini App) drifting after a location
  change; each caller believed it had the truth and overwrote selectively.
- **Hardcode to Moscow (UTC+3)** — rejected: breaks when Kai is in another timezone;
  task reminders fire at wrong local times with no indication of the error.
- **Derive tz from IP on each call** — not viable: Telegram bots receive no user IP.
  The Mini App backend can see the browser IP but it is unreliable (VPN, proxies) and
  adds a network call per request.
- **IANA timezone strings instead of integer offsets** — considered; not chosen.
  Integer offsets fit the use cases (schedule math, "today" anchor for LLM) without
  a timezone database dependency. DST is irrelevant for Russia (abolished 2014) and
  Kai's other common locations (Turkey: year-round UTC+3 since 2016). Worth
  revisiting if the system expands to DST-active regions or a multi-user deployment.

## Consequences

- **Offset and city are always updated together** — no partial drift between the two
  keys from split write paths. The Mini App and bot see the same truth after the next
  TTL expiry.
- **In-process cache converges immediately** after a bot-triggered update; cross-process
  writes (Mini App → PG → bot) converge within one 60-second TTL period.
- **Trade-off — TTL window:** in the 60 seconds after a cross-process write, the
  bot's in-process cache can be stale. Acceptable: a one-minute reminder skew is
  not user-visible at personal-practice scale.
- **Trade-off — `CITY_TZ` maintenance:** the lookup table is a hand-maintained dict
  in `core/location.py`. New cities or offset changes require a code change.
  Acceptable for a single-user system; a DB-backed table or IANA lookup would be
  needed for multi-tenant deployment.
