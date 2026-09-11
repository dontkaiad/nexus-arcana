# BOOKING — data-model contract (heylark Booking, #23 / ADR-0026)

Code conforms to: 24cb626. This spec describes the booking data model as of
that commit; update it in the same PR that changes the model.

> Contract, not snapshot. Describes the persistent model, the guarantees of
> each operation, and the invariants — the things that should not drift with
> ordinary commits. Enumerations point at the code constant that owns them
> rather than restating it. Every claim is verifiable from the files in
> "Verify against code". Design rationale and alternatives live in
> [ADR-0026](../CASES/0026-booking-architecture.md) — this doc is the
> up-to-date model, the ADR is the historical decision record.

## Purpose

Friends book Kai's time; Arcana guests request a tarot/ritual session.
`booking.heylark.dev` is the web front (three faces by role — guest, friend,
admin), `@heylark_booking_bot` (Zarya) is the chat concierge. Both talk
directly to the same `core/booking/` domain (PG) — no HTTP between them.
Free/busy is computed from Kai's own internal events (Nexus tasks, Arcana
works, other bookings, manual blocks), not an external calendar.

## One slot model, opt-in per context

`context ∈ ('friends', 'arcana')` on every table row, both driven by the
**same windows-based algorithm** (`core.booking.slots.free_slots`):
`booking_availability` rows for that `context` (recurring by `weekday` XOR
one-off by `specific_date`, #232) → concrete slots (stepped by
`slot_minutes`, meeting must fit before the window closes) → drop slots
inside `min_notice_hours` / past `max_advance_days` → drop slots overlapping
`busy_intervals()` (below), widened by each window's own buffers. **A day
with no matching `booking_availability` row shows zero slots** — nothing is
bookable until Kai explicitly opens a window for it.

#243 note: friends briefly had a windows-free "any free hour 13:00–23:00 MSK
is bookable" model (#233) — reverted after Kai found an always-wide-open
calendar unwelcome ("было бы лучше чтобы по умолчанию всё было занято и я
сама решала какие слоты выкатить"). Friends and Arcana now share the exact
same mechanism (add a window via `POST /booking/availability` with
`context="friends"` or `"arcana"`) — the only difference is who's allowed to
see/book the resulting slots (`core.auth_grants.booking_role`) and what
confirming a booking creates (Linkage, below).

## Free/busy aggregation

`core/booking/busy.py:busy_intervals(user_id, start, end)` returns every
`BusyInterval` (source `task`/`work`/`booking`/`block`) the owner is
committed to in `[start, end)`, merged via `merge_intervals()`:

- **Nexus tasks** — any active (not `Done`/`Archived`) task with a `deadline`
  is a busy point. A task with only a `reminder` is **not** busy (a reminder
  is a poke, not a commitment).
- **Arcana works** — any active work with `scheduled_at` set.
- **Bookings** — rows in `BLOCKING_BOOKING_STATUSES = ("pending", "confirmed")`,
  using their real `start_at`/`end_at`.
- **Manual blocks** — `booking_block` rows (vacation, "не беспокоить").

Task/work duration: `tasks.duration_min` / `works.duration_min` if set,
else `DEFAULT_BUSY_MINUTES = 60` (per-row fallback — see #241).

## Schema

Migrations: `d4e5f6a7b8c9` (initial tables), `f7c8b9a0d1e2` (`work_status`
gains `scheduled`), `a8b9c0d1e2f3` (`booking_availability.specific_date`,
#232), `b1c2d3e4f5a6` (`tasks.duration_min`/`shared`, `works.duration_min`,
`nexus_lists.shared`, #241/#242). SQLAlchemy Core mirror: `core/booking/tables.py`.

### `booking_availability` — arcana-context windows only (see above)

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | PK |
| `user_id` | Text | owner |
| `context` | Text | `'friends'` \| `'arcana'` — in practice only `arcana` rows drive slots |
| `weekday` | SmallInteger | nullable, 0=Mon..6=Sun — XOR with `specific_date` (CHECK `booking_availability_day_xor`, #232) |
| `specific_date` | Date | nullable — one-off window, XOR with `weekday` |
| `start_time` / `end_time` | Time | window bounds |
| `tz` | Text | default `'Europe/Moscow'` |
| `slot_minutes` | Integer | default 60 |
| `min_notice_hours` | Integer | default 12 |
| `max_advance_days` | Integer | default 60 |
| `buffer_before_min` / `buffer_after_min` | Integer | default 0 — widen the busy-check around each candidate slot |
| `active` | Boolean | default true |

### `booking_meeting_type`

Optional labeled meeting kinds a guest/friend can pick (`slug`, `title`,
`duration_min` default 60, `location_kind`/`location_value`,
`requires_approval`, `color`, `active`). Not required for a booking to exist.

### `booking`

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger | PK |
| `user_id` | Text | owner (Kai) |
| `context` | Text | `'friends'` \| `'arcana'` |
| `meeting_type_id` | BigInteger | FK → `booking_meeting_type.id`, `ON DELETE SET NULL` |
| `requester_tg_id` | BigInteger | nullable |
| `requester_name` | Text | resolved via `core.auth_grants.get_display_name(tg_id)` first (the `people` registry), falls back to whatever the caller passed |
| `requester_contact` | Text | free-form |
| `start_at` / `end_at` | TIMESTAMP(tz) | NOT NULL |
| `hours` | Numeric | nullable |
| `status` | Text | `'pending'` \| `'confirmed'` \| `'declined'` \| `'cancelled_by_owner'` \| `'cancelled_by_requester'` — see `BLOCKING_BOOKING_STATUSES` for which hold a slot |
| `note` | Text | **the meeting's purpose**, not the requester's name (#239) — "шашлыки в Токсово", not "встреча с Мишаней"; required (non-empty) for `context='friends'` (`BookBody` model_validator) |
| `source` | Text | `'web'` \| `'tg_dm'` \| … — where the booking came from |
| `hold_expires_at` | TIMESTAMP(tz) | nullable |
| `nexus_task_id` / `arcana_work_id` | Text | set by `link_booking` once confirmed (see Linkage) |
| `token` | Text | opaque id for guest-side status/cancel links |
| `created_at` / `decided_at` | TIMESTAMP(tz) | |

Confirmation rule: `status = "confirmed"` immediately for `context="friends"`
(trusted — Kai or an approved friend); `"pending"` for `context="arcana"`
(unknown guest, needs Kai's approval) — `miniapp/backend/routes/booking.py:
booking_book`.

### `booking_block`

Manual "unavailable" ranges (`start_at`, `end_at`, `reason`) — vacation, "не
беспокоить". Always UTC-aware `TIMESTAMP(tz)`; a caller passing a naive
datetime must attach an explicit offset (`+03:00` for Kai's MSK-entered
ranges) — `_parse_dt` treats a timezone-less string as UTC, not MSK (#240).

## Linkage (confirmed booking → Nexus/Arcana)

`core/booking/linkage.py`, direct SQLAlchemy-Core writes (Zarya's narrow
image ships the table defs but not the full repos):

- `context="friends"` → a `tasks` row: `title = "☕ " + note` (the purpose,
  #239) falling back to `"☕ Встреча: {requester_name}"` if `note` is empty;
  `note` field on the task = `"с {requester_name} · бронь #{id} · {hours} ч
  · {source}"`; `deadline` = booking start; no `reminder` (Zarya owns
  booking reminders, not the task).
- `context="arcana"` → a `works` row: `category = "🃏 Расклад"`,
  `status = 'scheduled'` (🗓 Запланировано, seeded by `f7c8b9a0d1e2`),
  `scheduled_at` = booking start.
- `link_booking(b)` is idempotent (repeat calls with an already-linked
  booking are a no-op) and writes the created id back onto
  `booking.nexus_task_id`/`arcana_work_id`.
- `unlink_booking(b)` archives the linked task/work on cancellation.
- `update_linked_time(b)` (#220/B7) re-points the linked task's `deadline` or
  work's `scheduled_at` after a reschedule; never raises if unlinked.

## API (`miniapp/backend/routes/booking.py`)

Auth seam: `booking_principal` (session cookie or service token) →
`Principal{role, uid, tg_id}`, `role ∈ {"admin","friend","guest"}` from
`core.auth_grants.booking_role`. Endpoints, non-exhaustive grouping (see the
file for the full route table):

- **Public read**: `GET /booking/public`, `/booking/slots`, `/booking/calendar`,
  `/booking/tip`, `/booking/me`, `/booking/request/{token}`, `/feed/{token}.ics`.
- **Booking**: `POST /booking/book` (owner gets a Zarya DM in addition to the
  log-topic post — `notify_user(..., bot="zarya")` to every `config.allowed_ids`
  entry, #238); `GET /booking/mine`; `POST /booking/{token}/cancel`.
- **History/reschedule** (#220/B7): `GET /booking/history` (any status, last
  50, newest first); `POST /booking/{booking_id}/reschedule` (conflict-checked
  against `busy_intervals` excluding the booking's own id, then
  `reschedule_booking` + `update_linked_time`, DMs the requester).
- **Owner admin** (role=admin only): `GET/POST/PATCH/DELETE /booking/availability`,
  `/booking/meeting-types`, `/booking/blocks`; `GET /booking/requests` +
  `POST /booking/requests/{id}/confirm|decline`; `GET /booking/feed-url`.

`AvailBody` requires exactly one of `weekday`/`specific_date` (XOR,
model_validator mirrors the DB CHECK). `BookBody` requires non-empty `note`
when `context="friends"`.

## Shared items (#242 — Zarya → friends)

Not part of `core/booking/` itself, but consumed by the same bot: Kai can
flag a Nexus task (`tasks.shared`) or a Списки item (`nexus_lists.shared`) as
visible to friends. `core/shared_items.py:shared_items_summary(user_id)`
returns a short "title (human date hint)" list of active shared items
(`sold`/`archived` excluded), which `zarya/classifier.py` mixes into the
system prompt for `role="friend"` chat replies — Zarya decides in-character
whether and how to mention them, never reads them out as a list. Setting the
flag: tasks via the NL edit-record flow ("расшарь задачу X" / "скрой задачу
X", `core/classifier.py:_SHARE_RE` → `nexus/handlers/tasks.py:_apply_edit`
field `"shared"`); list items have no NL setter yet, only the column.

## Duration (#241)

`tasks.duration_min` / `works.duration_min` (nullable Integer) override the
default 1h busy-interval length per row (see Free/busy aggregation above).
Set via the same NL edit-record flow, field `"duration"` — human text
("2 часа", "30 минут", "полчаса") parsed by `core/duration.py:
parse_duration_minutes`, not by the LLM. Only wired for Nexus tasks; the
Arcana `works.duration_min` column exists and is honored by the busy
calculator, but has no NL setter of its own yet.

## Zarya (`@heylark_booking_bot`)

Deterministic-first: `wants_slots()` regex + slash commands handle ordinary
traffic at zero LLM cost; only genuinely unrecognized text reaches
`zarya/classifier.py:classify_zarya()` (one Haiku call). Group-chat gate:
`_bot_addressed()` requires a DM, an `@mention`, or a reply to Zarya's own
message before either the regex path or the Haiku fallback responds — Zarya
never reacts to un-addressed group chatter. Non-owner users cannot add Zarya
to a group (`on_membership_changed` kicks her back out).

## Verify against code

- `core/booking/tables.py` + `alembic/versions/{d4e5f6a7b8c9,f7c8b9a0d1e2,a8b9c0d1e2f3,b1c2d3e4f5a6}` — schema
- `core/booking/busy.py` — free/busy aggregator, per-row duration
- `core/booking/slots.py` — `free_slots`, one windows-based algorithm for both contexts (#243)
- `core/booking/repo.py` — CRUD, `reschedule_booking`
- `core/booking/linkage.py` — `link_booking`/`unlink_booking`/`update_linked_time`
- `core/auth_grants.py` — `booking_role`, `get_display_name` (`people` registry)
- `core/shared_items.py` — `shared_items_summary`
- `core/duration.py` — `parse_duration_minutes`/`format_duration`
- `miniapp/backend/routes/booking.py` — full route table + `AvailBody`/`BookBody` validators
- `miniapp/backend/booking_web/index.html` — web front (guest/friend/admin faces)
- `zarya/` — bot; `zarya/classifier.py` — Haiku fallback + shared-items prompt injection
- `nexus/handlers/tasks.py:_apply_edit` — `duration`/`shared` field branches
- `core/classifier.py` — `_EDIT_RE`/`_SHARE_RE`/`_EDIT_PARSE_SYSTEM`
