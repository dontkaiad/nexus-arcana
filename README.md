# ☀️ Nexus & 🌒 Arcana

> Two production Telegram bots + a Mini App. Not a tutorial project — I designed,
> built, and use them every day as my personal assistant and practice CRM.
> **☀️ Nexus** is my ADHD assistant: tasks, finances, budgets, reminders.
> **🌒 Arcana** is the CRM for a small esoteric practice: clients, tarot sessions, rituals.

![Python](https://img.shields.io/badge/Python-3.9-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Vite-61DAFB?logo=react&logoColor=white)

---

## Demo

| ☀️ Nexus — day | 🌒 Arcana — night |
| :---: | :---: |
| ![Nexus day](docs/assets/nexus.png) | ![Arcana night](docs/assets/arcana.png) |

![day → night transition](docs/assets/transition.gif)

> _Image placeholders — drop the files into `docs/assets/`:_
> `nexus.png` (Nexus day screen) · `arcana.png` (Arcana night screen) ·
> `transition.gif` (the sunset → moonrise animation when switching between the two bots).

---

## LLM engineering

This is the part I care about most — making LLM features that actually hold up in
daily production use, at a cost I can justify paying out of pocket.

### Cost-aware model routing

Haiku is the default for everything: intent classification, JSON parsing, spell
correction, layout conversion, ADHD tips. Sonnet is allowed only where the task
genuinely requires reasoning or empathy — tarot interpretation, budget planning,
long-form ADHD advice, vision analysis. That discipline is enforced by a test
(`tests/test_models_audit.py`) that fails if Haiku disappears from any routine
path or Sonnet appears somewhere it shouldn't. Cost decisions without enforcement
are not decisions.

→ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

### Vector semantic recall

Past tarot readings are indexed as triplets (cards · question · interpretation)
using **Voyage AI `voyage-4-lite`** embeddings (dim 1024) stored in **pgvector**,
in the same Postgres the rest of the system already runs. Cosine search returns
semantically similar past readings to ground the current interpretation in the
practitioner's actual history.

The design choice: pgvector in an existing Postgres instead of a standalone vector
database. At the scale of a personal practice — thousands of vectors, one user —
a separate service adds operational overhead and RAM cost with no benefit. The
retrieval port means swapping to Qdrant later is an adapter swap, not a rewrite.
Voyage's 3 RPM free-tier limit is handled by batch embedding: N triplets in a
session = one Voyage call, not N.

→ [ADR-0006](docs/CASES/0006-rag-vector-backend.md)

### Authored-voice RAG

Arcana has two interpretation modes. **Mode B** is the standard LLM path: generate
a reading from the deck reference, memory, and past sessions. **Mode A** is what
I reach for most: I dictate terse per-card accents ("ace — breakthrough", "lovers —
choice"), and the model *expands* those accents into full prose grounded in the deck
reference — without inventing meaning that isn't in my note or the card.

The product instinct behind this: the value of the CRM is *my* voice, not a
plausible synthesis. If the model rewrites the interpretation, the record stops
being mine. More concretely, those records feed the RAG corpus — a substituted
interpretation poisons future recall with generic LLM tarot language. Mode A
interpretations are the only ones indexed; mode B records are saved to the database
but excluded from the vector store. Corpus purity enforced structurally, not by
hoping mode B stays rare.

→ [ADR-0015](docs/CASES/0015-voice-authorship-mode-a.md)

### Deterministic card parser

The Waite deck has 78 cards with canonical Russian names. Before this, the parser
was LLM-based — which meant occasional hallucinations and non-deterministic
canonicalization. I replaced it with a decision-tree exact-match + fuzzy-match
pipeline over a hardcoded reference. The LLM is not involved; it cannot invent a
card that doesn't exist.

The principle: when the vocabulary is finite and closed, determinism beats
heuristics. LLM involvement here adds cost and variance, not value.

→ [ADR-0013](docs/CASES/0013-waite-deterministic-card-parser.md)

### Resilient LLM calls

All Claude API calls go through `core/claude_client.py` under a `retry_transient`
decorator: up to 3 attempts, exponential backoff with jitter, `Retry-After`
respected on 429s, 60-second timeout per call, SDK-level retries disabled to avoid
double-retry. Only transient errors retry (429, 5xx, timeout, connection); 4xx
client errors fall through immediately to a graceful fallback. Whisper in
`core/voice.py` follows the same pattern. A direct `client.messages.create` outside
this wrapper is a bug; `tests/test_llm_retry.py` makes it a failing test.

### Voice and vision

Voice notes are transcribed via **OpenAI Whisper** (`whisper-1`) and routed through
the same intent classification as text. Both bots handle voice input: Nexus creates
tasks and expenses from voice; Arcana records tarot sessions and client notes.

**Claude Vision** handles: receipts (expense parsing), tarot spread photos (card
identification), and Telegram profile screenshots (client onboarding). Vision is
Sonnet — one of the few cases where the model tier is non-negotiable.

---

## Architecture

The system follows a **ports-and-adapters** discipline inside a modular monolith.
Handlers never see SQL; the domain never sees the backend. This made a live migration
from Notion to PostgreSQL a controlled direct cutover per domain slice — no dual-write
period, no rollback drama.

`core/` holds all shared logic (memory, payments, reminders, preprocessing, RAG,
location). `nexus/` and `arcana/` are domain packages that only talk to `core/`
interfaces. The Mini App backend (`miniapp/backend/`) runs as a separate FastAPI
process but shares the same Postgres and `core/` modules.

**16 Architecture Decision Records** in [`docs/CASES/`](docs/CASES/) covering:
RAG vector backend and right-sizing, voice authorship and RAG corpus gating,
deterministic card parser, CI/CD forced-command deploy, single-writer location,
Notion→PG migration strategy and execution, access model, and more. Each ADR states
the rejected alternatives and the trade-offs — not just what was chosen.

**10 domain specs** in [`docs/specs/`](docs/specs/) — Tasks, Finance, Sessions,
Rituals, Clients, Memory, Lists, Grimoire, Works, Budget — each pinned to a commit
hash and updated in the same PR that changes the model.

→ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Engineering practice

**CI/CD:** every push to `main` deploys to Vultr VPS via GitHub Actions. The deploy
key uses a forced command in `authorized_keys` — the key can only trigger `deploy.sh`,
nothing else. Failure detection is marker-based (ssh-action swallows exit codes, so
`deploy.sh` prints `deploy done` as its final line only on success; the CI step greps
for it). Telegram alert on failure.
→ [ADR-0014](docs/CASES/0014-deploy-forced-command-marker.md)

**Test coverage:** the suite covers intent classification, LLM retry logic, model
routing (Haiku/Sonnet whitelist), repo contracts, RAG indexing gates, card parsing,
CORS config, and more. Every commit runs the full suite before push; a red suite
blocks the commit.

**Single-writer location:** timezone offset and city are owned by one module
(`core/location.py`). All callers — bot handlers and the Mini App backend — write
through `set_user_location`; nothing reaches the storage keys directly. Enforces that
a location change propagates to reminders, date parsing, and weather simultaneously.
→ [ADR-0016](docs/CASES/0016-single-writer-location.md)

---

## What it does

**☀️ Nexus** — personal ADHD assistant:
- Tasks with deadlines and reminders, captured by voice, text, or photo
- Finance tracking: expenses from receipts (Vision), budget allocation with Sonnet reasoning
- Reply-to-edit: reply to any bot confirmation to amend the record in place
- Mini App dashboard for review across tasks, finances, and habits

**🌒 Arcana** — CRM for a tarot practice:
- Voice-first session logging: dictate the reading, the bot structures and stores it
- Client management with session history, payment tracking, barter support
- Ritual logging with inventory writeoff
- Semantic recall: past readings surface into current interpretation context
- Mini App with session history, client cards, and practice stats

Both bots share the same `core/` layer: memory, payments, reminders, preprocessing
(layout conversion EN→RU, spell correction), subtask handling, and message-to-record
mapping.

---

## Stack

**LLM**
- Claude API — Haiku (routing, parsing, spell), Sonnet (interpretation, reasoning, vision)
- Voyage AI — `voyage-4-lite` embeddings (RAG)
- OpenAI Whisper — voice transcription

**Backend**
- Python 3.9, aiogram 3.x, FastAPI
- SQLAlchemy Core + Alembic
- PostgreSQL 16 + pgvector extension
- SQLite (pending state: 8 in-process stores)

**Infrastructure**
- Docker + docker-compose, Vultr VPS
- GitHub Actions (CI/CD), forced-command deploy
- Cloudinary (media storage)

---

## Status

Personal production system — one user (me), running 24/7, actively developed as my
AI/LLM engineering proving ground. Architecture, specs, UX decisions, and code review
are mine; execution is AI-augmented under detailed specs, and every change ships with
a green test suite.

By Kai Lark ([@hey_lark](https://t.me/hey_lark))
