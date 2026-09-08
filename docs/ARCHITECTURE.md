# Architecture

> Code conforms to: `0680150` · Update in the same PR that changes the architecture.
> This is an engineering overview, not a developer spec. For the data model and
> contracts, see [`docs/specs/`](specs/) (11 domain specs) and the ADRs in
> [`docs/CASES/`](CASES/) (24). Read time: ~12–15 min.

## What this is

Two Telegram bots and a Mini App on a shared `core/`: ☀️ **Nexus** is my personal
assistant — tasks, finance, budget limits, ADHD-friendly nudges; 🌒 **Arcana** is the
CRM for a small esoteric practice — clients, tarot sessions, rituals, the work pipeline
that ties them together. Each lives as **two surfaces of one system**: a Telegram bot
for capturing things on the go — a quick line of text or a voice note while I'm
mid-thought — and a FastAPI + React/Vite **Mini App** for reviewing and managing them on
a real screen. That split is deliberately ADHD-shaped: the friction of *recording* a
thought has to be near zero, while *seeing* the whole picture deserves room to breathe.
I designed and wrote the whole front end by hand, animations and all. Claude does the
language work — intent classification, field parsing, tarot interpretations — routed by
cost, with voice and photos handled too (more in the stack).

I'll be honest about scale, because it's the context that explains every decision
here. **Today there's one user — me — and I'm both the user and the engineer.** That's
a fact about now, not a ceiling: the data layer is user-scoped end to end and
fail-closed per user, so multi-user isolation is already in the code, not a
rewrite waiting to happen. I built it that way on purpose — if this ever becomes a
product other practitioners use, the architecture goes wide without me unpicking
single-tenant assumptions. The bots run **24/7 in production on a VPS** — real money
moves through the finance domain, real client data lives in the CRM — so I hold a
team-grade bar regardless of headcount: versioned migrations, green tests on every
commit, ADRs written *before* I commit to a decision. And there's a second thing this
system honestly is: it's where I grow as an engineer. A real production system I depend
on daily doubles as my proving ground for AI/LLM engineering — cost-aware routing,
resilient LLM plumbing, retrieval — learned by shipping and maintaining, not by
tutorial. The question I care about isn't "can it scale to a million users" — it's "can
one person keep a multi-domain production system honest, cheap, and easy to change
while learning on it." That's the discipline the rest of this document is about.

## Decisions I'm proud of

### 1. Direct cutover from Notion to PostgreSQL — no dual-write

**Context.** The whole system started with Notion as the database (more on that in
*How it evolved*). Migrating a live datastore, the textbook answer is dual-write:
write to both backends in parallel, reconcile, then flip. I wrote that ADR first
([ADR-0009](CASES/0009-migration-execution.md)).

**Choice.** I threw it out and did a **direct cutover** per domain
([ADR-0010](CASES/0010-migration-direct-cutover.md)): design the PG schema → Alembic
migration → point the repository adapter at Postgres → verify → decommission the
Notion path for that slice.

**Alternatives rejected.** Dual-write, and "dual-write kept just to rehearse the
pattern." I rejected both because the two conditions that justify dual-write didn't
hold: no concurrent live traffic during the move, and the Notion source held only test
data — nothing to reconcile. Carrying parallel-write code, a reconciliation step, and a
`notion_id` linking column to protect against a risk that doesn't exist is the opposite
of the signal I want to send.

**Trade-off.** The cutover is **not zero-downtime** — acceptable here, would be
unacceptable with live users. I paid for the simplicity with a hard rule: I never
delete the old path on a guess. I prove it's dead first (see *How it evolved*).

### 2. Single-owner identity hub — `clients.id` is the one place a client exists

**Context.** A client shows up across sessions, rituals, and works. The lazy version
duplicates the client's name/type onto each record, or threads relations every which
way.

**Choice.** `clients.id` is the **single hub**. Sessions, rituals, and works each carry
a nullable `client_id` FK pointing back to it; nobody duplicates client identity. When
the bot extracts a client name from a message, `core/client_resolve.py` resolves-or-
creates exactly one row and hands back its id ([CLIENTS spec](specs/CLIENTS.md)).
User identity is the same shape one level up — `core_identity` is authoritative, keyed
by the id every other table references in its `user_id` column (renamed from
`user_notion_id` in #144; the PK keeps the name `notion_id`,
[ADR-0024](CASES/0024-identity-pk-keeps-notion-id-name.md)). The single owner's two
Telegram accounts fold to one shared `user_id` so owner-scoped reads never fragment
across devices (#202, migration `e067a1b2c3d4`) ([ADR-0007](CASES/0007-identity-pg.md)).

**Alternatives rejected.** Denormalizing client fields onto each event record (fast
reads, but every rename/retype becomes a migration and the data drifts), and a generic
polymorphic "entity" table (flexible on paper, untyped and unjoinable in practice).

**Trade-off.** A session with no client is a legit state (a reading for myself), so
`client_id` is nullable — which means "client vs. self" is a query condition I have to
get right everywhere, not a guarantee the schema makes for me. I took that cost on
purpose: one source of truth for who a client is beats a schema that can't drift but
also can't tell you anything.

### 3. `work_id` foreign key, not a junction table (Work ↔ Session/Ritual)

**Context.** A planned **Work** (the pipeline item — "do a protection ritual for X")
gets fulfilled by exactly one concrete Session or Ritual, which then closes it.

**Choice.** A nullable `work_id` FK on the session/ritual row, `ON DELETE SET NULL`,
indexed. Creating the event finds the one open Work for that client+category, stamps
`work_id`, and marks the Work done — one transaction, in `core/work_relation.py`.

**Alternatives rejected.** A `work_links` junction table. I rejected it because the
cardinality is genuinely **1:1** — I confirmed it from the real code (single relation,
`page_size=1`, immediate close), not from a guess about future flexibility. A junction
table models many-to-many; using one for a 1:1 relationship is over-engineering that
buys a JOIN and a second write for a degree of freedom the domain doesn't have. If the
cardinality ever changes, *that's* when the junction earns its place — and it's a clean
migration, not a rewrite.

**Trade-off.** If I'm wrong about 1:1, I migrate later. I'd rather migrate a wrong-but-
simple model than maintain a right-but-speculative one. See [WORKS spec](specs/WORKS.md).

### 4. SQLAlchemy Core, not the ORM; sync engine wrapped in `asyncio.to_thread`

**Context.** aiogram is async; the persistence layer has to not block the event loop.

**Choice.** **SQLAlchemy Core** — explicit `select`/`insert`/`update` against Table
objects — driving a synchronous psycopg2 engine, with each repo method running its sync
block inside `asyncio.to_thread(...)`. No ORM session, no identity map, no lazy loading.

**Alternatives rejected.** The full ORM (I don't want a unit-of-work and lazy-load
surprises for what is fundamentally CRUD-with-lookups), and a native async driver like
asyncpg/SQLAlchemy-async (real benefit, but it colors the entire stack async and adds a
dependency; my query volume is tiny and `to_thread` is honest about what's actually
happening — a blocking call moved off the loop).

**Trade-off.** `to_thread` has per-call thread-pool overhead and I write my SQL by hand
instead of getting it generated. For this workload that's a feature: the queries are
legible, I see every column, and there's no ORM magic between me and Postgres. If
throughput ever demanded it, the repo seam (next decision) means I can swap the driver
without touching a single handler.

### 5. Ports-and-adapters — handlers never see SQL, the domain never sees Notion

**Context.** When everything ran on Notion, Notion's property-dict format leaked into
the handlers. That coupling is exactly what made the backend painful to change — the
lesson that shaped this layer.

**Choice.** A strict seam, four layers deep:
`handler → repo (port) → pg_*_repo (adapter) → Table`. Handlers and Mini App routes call
domain methods (`list_open`, `set_result`, `set_props`) and get back **plain domain
dataclasses** — `Ritual`, `Client`, `Work`, `Task`, `Memory`. The repo seam
(`core/repos/lists_repo.py`, `nexus/repos/tasks_repo.py`) is the only thing that knows a
database exists; the adapter beneath it is the only thing that knows it's Postgres.

**Alternatives rejected.** Two options, both worse at the ends of a spectrum. **Letting
handlers call the PG adapter directly** removes a layer but puts persistence detail
straight back into business logic — the precise coupling I was migrating away from.
**A full hexagonal framework** with formal abstract interface classes per port adds
ceremony for an indirection I can already get from a module boundary; the seam is the
value, the interface scaffolding isn't.

**Trade-off.** More files and a little boilerplate per domain. It paid for itself the
moment the Notion removal became a *runtime-clean* operation: the final cutover deleted
the entire Notion wrapper and a canary grep found **zero** Notion imports or SDK calls
left in `core/`, `nexus/`, `arcana/`, `miniapp/`. The seam is why that was a deletion,
not a rewrite.

### 6. Cost-aware model routing — Haiku by default, Sonnet only where it earns its price

**Context.** I pay for every token out of my own pocket. Most LLM calls here are
mechanical: classify an intent, pull two fields out of a sentence, correct a typo.

**Choice.** **Haiku is the default** for everything routine — the intent router, all
JSON field parsers, spell-correction, the one-line ADHD tip. **Sonnet is reserved** for
the few places that genuinely need reasoning or empathy: tarot interpretations, the
budget narrative, vision (receipt photos), session summaries. Every Anthropic call goes
through one client wrapper with retries/backoff so resilience isn't reinvented per call
site.

**Alternatives rejected.** Sonnet everywhere (simpler, and quietly 10–20× the cost for
no quality gain on a field-extraction task), and hand-tuned per-call model strings
(drifts the instant someone copy-pastes a call).

**Trade-off.** Routing by task means a tighter prompt budget for the cheap path and a
rule I have to keep honest. So I made the rule executable: **`tests/test_models_audit.py`
is a guard that fails the build if Haiku ever disappears from the router/parsers or
Sonnet creeps somewhere it shouldn't.** A cost decision that isn't enforced is a cost
decision that regresses on the next refactor.

### 7. Vector semantic recall — pgvector + Voyage, in the Postgres I already run

**Context.** I wanted past readings searchable by *meaning*, not keyword — so the
interpretation prompt can surface a client's own similar history and keep my voice
consistent. The vectors used to live in a Qdrant container that belonged to a
*neighbouring* bot, reached over an external Docker network; when that bot was down the
network vanished and recall silently became a no-op.

**Choice.** I decoupled it: reading triplets (cards · question · interpretation) are
embedded with Voyage `voyage-4-lite` (dim 1024) and stored in **pgvector, in the same
Postgres** the bot already runs — one store, no extra service. History is backfilled,
and the whole path is graceful: no Voyage key / DB down / pgvector absent → warning +
no-op, never a failed reading. Embedding is batched (one reading = one Voyage call) to
respect the free tier's 3 RPM.

**Alternatives rejected.** Staying on the neighbour's Qdrant (made recall hostage to an
unrelated bot's uptime, and Qdrant's real zone — millions of vectors — is a scale this
personal practice will never reach), and keyword-only recall (the deterministic layered
prompt assembly already covers context-by-key; it can't do similarity by meaning).

**Trade-off.** pgvector + HNSW is right-sized for thousands of vectors, not millions; at
that scale I'd re-open the choice. I held off committing until I'd actually decided
rather than reaching for a vector DB by reflex — the reasoning is in ADR-0006. Memory
rows and other sources beyond reading triplets stay on deterministic/keyword recall for
now.

## How it's built

```mermaid
flowchart TD
    TG([Telegram]) --> H[aiogram handlers<br/>Nexus · Arcana]
    WA([Mini App · React/Vite]) --> API[FastAPI routes]

    H --> CORE[core/ shared logic<br/>preprocess · client_resolve<br/>reminders · message_pages]
    API --> CORE

    H -. voice .-> WH[[OpenAI Whisper<br/>transcription]]
    H -. photo · text .-> LLM{{Claude API · Haiku default<br/>Sonnet targeted · Vision for photos}}
    CORE -. interpret · parse · classify .-> LLM
    H -. media .-> CDN[(Cloudinary<br/>photo storage)]

    CORE --> PORT[repo seam / ports<br/>lists_repo · tasks_repo]
    API --> PORT
    PORT --> ADP[pg_*_repo adapters<br/>SQLAlchemy Core]
    ADP --> DB[(PostgreSQL<br/>domain + lookup tables)]

    ADP -. asyncio.to_thread .-> ADP
```

**Stack.** Python 3.9, aiogram 3.13 (bots), FastAPI + React/Vite (Mini App),
PostgreSQL 16, SQLAlchemy Core + Alembic (40+ migrations and counting). Claude API
(Haiku 4.5 / Sonnet 4.6) for language, plus **Claude Vision** for photos — parsing
receipts and tarot spreads. Voice notes are transcribed via the **OpenAI Whisper API**
(`whisper-1`), wired through `core/voice.py` — supported in both bots. Media (client /
object / session photos) is stored in **Cloudinary** via signed upload. A substantial
test suite, green and zero-skipped on every commit.

**Data flow.** Input arrives in three shapes — text, a voice note (→ OpenAI Whisper →
text), or a photo (→ Claude Vision, e.g. a receipt or a tarot spread); any uploaded media
lands in Cloudinary. From there a message hits a handler → `core/preprocess.py` normalizes
it (keyboard-layout fix + guarded Haiku spell-correction) → an intent router (Haiku)
dispatches it →
the domain handler parses fields, resolves the client/identity, and writes through the
repo seam → a confirmation goes back, often with inline buttons and a reply-to-edit
hook so I can amend the record by replying to the bot's message.

**Lookup tables over enums.** Statuses, priorities, types, payment sources, outcomes
live in FK'd lookup tables, not string columns or Python enums
([ADR-0008](CASES/0008-lookup-tables.md)) — so adding a category is a row, the labels
are canonical, and the schema enforces them.

**Deploy.** Docker Compose runs Postgres 16 + the two bot services on a Vultr VPS;
GitHub Actions deploys over SSH on push to `main`. A **Caddy** reverse proxy terminates
TLS for the Mini App under my own domain, **heylark.dev** — which I bought partly so I
could reach the system comfortably from my laptop, not only inside the Telegram Mini
App. A separate, isolated dev Postgres stack keeps experiments off production, and prod
is backed up nightly with a restore I've actually run — a backup you haven't restored is
a hope, not a backup.

## How it evolved

This didn't start as a Postgres system. It started with **Notion as the backend** —
databases as tables, the API as my data layer. That was the right call to *begin* with:
I got a usable CRM and a working bot in days, with a UI I could eyeball, and zero schema
work up front. It stopped being the right call when the domains grew relationships
Notion can't model cheaply (the Work↔event pipeline, the client hub) and when ~250ms of
API latency sat on the critical path of every permission check.

So I migrated, one vertical slice at a time, ordered by risk — rituals first (most
self-contained), identity and finance later (most connected). The part I'm proudest of
isn't the schema; it's the **method**. I never deleted a Notion path because I assumed
it was dead — I proved it with a **canary grep** across the runtime and confirmed the
*production environment carries no Notion token at all*, so every remaining Notion code
path was provably unreachable before I removed it. When I found a "dead" path that was
still live (the Mini App's Today tab, a finance gate, a couple of backfill scripts), I
stopped and said so instead of deleting it. The final step shrank the Notion wrapper to
a tiny read-adapter used *only* by the migration scripts, with the runtime verified
Notion-free.

Two other things grew alongside the backend. The **Mini App came after the bots** — once
the data was rich enough to be worth *seeing*, not just typing at. I designed and wrote
the whole front end myself: the animations, the holo-foil "self-client" card, and the
day/night identity of the two bots — switching from Nexus to Arcana plays as a literal
sunset into moonrise (☀️ → 🌒), the sun going down as the moon comes up, and back again
the other way. None of it generated, all of it mine. I mention it because the signal here
isn't only backend; I care how the thing feels to use, and I can build that end too.

And the box it runs on was a decision, not a default. First of all it had to get *off my
MacBook*: the bot is where I capture things the instant they occur, so the system can't
depend on whether my laptop is awake. I first planned a **Raspberry Pi 3** — cheap, mine,
always-on, enough for two bots. As the system grew (two bots + Mini App + Postgres + LLM
routing) I moved to a **VPS** on purpose: right-sized to the actual load rather than
over-provisioned from day one or wedged onto hardware it had outgrown. Always-available
first, then right-sized — I'd rather upgrade infrastructure when the system earns it than
pay for scale I'm only guessing at.

The 11 specs in [`docs/specs/`](specs/) are written the same way: each one documents the
code as it *is*, carries a conforms-to hash, and points at the files you can check it
against — no aspirational data models, no "known limitations" prose (those are issues).
A spec that describes the ideal instead of the real is a lie with a nice font.

## Reference

Onboarding-level detail: where the model calls go, what state lives outside Postgres,
the patterns worth learning once, and the mistakes the codebase has already made.

### Model routing

Haiku by default, Sonnet only where reasoning or empathy earns the price (decision 6).
[`tests/test_models_audit.py`](../tests/test_models_audit.py) fails the build if this
table drifts.

| Where | Model | Why |
|---|---|---|
| Intent router (`arcana/handlers/base.py`, Nexus dispatch) | **Haiku** | 8-shot classification into a fixed label set |
| All JSON field parsers — sessions, rituals, works, clients, lists, reply-update, delete-intent | **Haiku** | pull 2–8 named fields out of one message |
| Spell-correction (`core/preprocess.py`) | **Haiku** | whitelist-guarded typo fix over a closed vocabulary |
| Keyboard-layout convert (`core/layout.py`) | *no LLM* | deterministic QWERTY↔ЙЦУКЕН map |
| Waite card parser (`core/waite_cards.py`) | *no LLM* | 78-card closed set, decision-tree + fuzzy match ([ADR-0013](CASES/0013-waite-deterministic-card-parser.md)) |
| ADHD tip on the Mini App home | **Haiku** | one 15-word line |
| Tarot interpretation — mode A expand & mode B generate (`arcana/handlers/sessions.py`) | **Sonnet** | narrative + the practitioner's voice ([ADR-0015](CASES/0015-voice-authorship-mode-a.md)) |
| Session summary (`miniapp/backend/routes/arcana_sessions.py`) | **Sonnet** | synthesise across N triplets |
| Budget analysis (`core/budget.py`) | **Sonnet** | multi-constraint planning narrative ([ADR-0022](CASES/0022-budget-arithmetic-determinism.md) keeps the *arithmetic* deterministic) |
| Long-form ADHD advice, category 🦋 (`core/memory.py`) | **Sonnet** | empathy + length |
| Vision — receipts, tarot-spread photos, TG screenshots (`core/vision.py`) | **Sonnet** | tier is non-negotiable for image input |

Every call goes through `core/claude_client.py` (`retry_transient`: 3 tries, backoff+jitter,
`Retry-After` honoured, 60 s timeout, SDK retries off). A raw `client.messages.create`
outside it is a bug — [`tests/test_llm_retry.py`](../tests/test_llm_retry.py) enforces it.
Voyage (`voyage-4-lite`, dim 1024) for embeddings + a Haiku reranker over its candidates
([ADR-0021](CASES/0021-memory-rag-reranking.md)); Whisper `whisper-1` for voice.

### State outside Postgres

Postgres is the source of truth for every domain. A handful of **process-local SQLite
files** hold interaction state and caches — auto-created on first run (`CREATE TABLE IF
NOT EXISTS`), TTL-swept, safe to delete. Ephemeral by design: losing them costs an
in-progress dialog or a cache warm-up, never a record.

**Pending-dialog stores** (a bot question is open, waiting for the next message /
button):

| File | Flow | TTL |
|---|---|---|
| `pending_tarot.db` | Arcana session parse / preview / intent & client disambiguation | 1 h |
| `pending_works.db` | Arcana work preview-flow (`work_preview.py`) | 30 m |
| `pending_tasks.db` | Nexus task preview & deadline/clarification | 30 m |
| `pending_lists.db` | «📋 Подзадачи» checklist input (`subtasks_handler`) | 30 m |
| `pending_clients.db` | Client info-collection dialog | 10 m |
| `pending_client_photo.db` | `/client_photo` name + photo flow | 10 m |
| `pending_writeoff.db` | Ritual consumables write-off edit | 30 m |
| `pending_barter.db` | «Что в бартере?» reply | 30 m |
| `pending_grimoire_search.db` | Grimoire search query input | 10 m |
| `pending_note_edit.db` | Nexus note tag edit | 10 m |

`pending_kv.db` is the generic version of the pattern (`core/pending_kv.py`): one table
keyed `(uid, kind)`, JSON value, per-call TTL — new pending flows use it instead of
hand-rolling a store or an in-memory dict.

**Caches / mappings** (rebuildable, not dialog state): `message_pages.db` — `chat:msg_id
→ page_id` so a reply to any bot message edits that record (30 d); `session_cache.db` —
session/theme summary cache; `spell_whitelist.db` — the spell-correction whitelist
(cards + terms + client names), invalidated on client create (1 h); `task_reminder_msg.db`
— reminder-message tracking; `ru_calendar.db` — RU work-calendar holidays (30 d);
`nexus_streaks.db` — per-task + global daily streak store (persistent, [ADR-0023](CASES/0023-streaks-out-of-scope-for-arcana-works.md)).

> One known wart: a few Nexus **finance** sub-flows still hold pending state in an
> in-memory dict — lost on restart (issue #208). The non-money clarify-dialogs were
> moved to `pending_kv`.

### Patterns worth learning once

Both bots are sisters — most UX primitives are shared and live in `core/`. Reimplementing
one per-bot is treated as a bug.

- **Preview-flow** (`arcana/handlers/work_preview.py`, Nexus tasks) — parse → show a
  preview with `[✅ Сохранить]` → only then write. Nothing hits the DB before confirmation.
- **`resolve_or_create` / `resolve_self_client`** (`core/client_resolve.py`) — any client
  name a parser extracts goes through one resolver that finds-or-creates exactly one row,
  announces «🆕 Создала клиента X», and wires a reply hook to change the type. No handler
  writes a client-typed record without a client relation.
- **`work_relation.link_practice_record`** (`core/work_relation.py`) — on a done
  session/ritual, find the client's open Work of that category *or create one*, link the
  event's `work_id`, close the Work — one transaction. Every performed event ends up with
  a Work in history.
- **`message_pages` + `reply_update`** — the reply-to-edit spine. `message_pages.db` maps
  a bot message back to its record; `core/reply_update.py` parses the reply and applies a
  field-level patch. Works across tasks, notes, sessions, rituals, clients.
- **`message_collector`** (`core/message_collector.py`) — 5-second debounce so a burst of
  quick lines is processed as one thought, not five.
- **Reminder scheduler** (`core/reminder_scheduler.py`) — one APScheduler flow for both
  bots. Jobs are in-memory (lost on restart) and **restored from the columns on startup**;
  a fired one-off reminder nulls its column so a restart can't re-send it (#206).
- **Subtasks factory** (`core/subtasks_handler.py`) — one `make_subtasks_router()` factory,
  not one Router mounted twice (aiogram forbids that).
- **Lookup tables, not enums** — a new status/category is an INSERT, and the schema
  enforces the set ([ADR-0008](CASES/0008-lookup-tables.md)).

### Anti-patterns (the codebase has made these)

- **Handler calls the PG adapter directly** — persistence detail back in business logic,
  the exact coupling the seam exists to prevent.
- **Parallel per-bot implementation** of a shared primitive — if Arcana seems to need
  different UX than Nexus, that's a question for the owner, not a fork.
- **Pending state in an in-memory dict** — vanishes on restart / redeploy; use
  `core/pending_kv.py` (or a dedicated `pending_*.db`).
- **Deleting a "dead" code path on a guess** — prove it unreachable first (canary grep,
  prod has no token for it), then remove. Two "dead" Notion paths turned out live.
- **Sonnet where a regex or Haiku would do**, or a bare `client.messages.create` — both
  fail a guard test.
- **A spec that describes the ideal instead of the real** — every spec pins a commit hash
  and a file list you can check it against.

## What's next

In order — because sequencing is part of the engineering:

- **Make sense of the test suite first.** It accumulated across the migration at varying
  maturity — some tests are sharp, some are scaffolding I've outgrown. Before I add new
  *kinds* of testing I want to audit, structure, and prune what's there, so the suite is a
  tool I trust rather than a count I quote.
- **Then evals + observability.** The model-audit guard is a start, but I want
  structured evals on the interpretation/parse paths and real tracing on the LLM calls,
  not just logs — so I can tell whether a prompt change made things *better*, not just
  different.
- **The Works epic** — deepening the planned-work ↔ fulfilled-event pipeline. The FK
  foundation is in place and the Mini App now surfaces both directions (planned badge,
  `from_work` reverse-link, create-from-app, a "done" tail so finished practice stays
  visible); next is richer planning ergonomics on top of it.
- **Apple-ecosystem integration** (planned, not built) — native reach into the
  macOS/iOS side I actually live in, so the assistant meets me where I work.

The through-line, and where I'm taking this: **AI/LLM engineering** — cost-aware routing,
resilient LLM plumbing, and retrieval, done with the same honesty as the rest of this
system, and treated as something I'm actively *learning* on a system that has to keep
working while I do. I'd rather ship a small thing I fully understand than a big one I'm
pretending to.
