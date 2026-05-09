# 🌗 Nexus & Arcana

Two Telegram bots and a Mini App for managing both halves of life — a personal assistant for tasks, finances, and notes (Nexus), and a CRM for an esoteric practice (Arcana).

Public source. Built and maintained as a single-developer system.

---

## About

Designed and architected by **Kai Lark** ([@hey_lark](https://t.me/hey_lark)) — IT product manager with 6 years of experience.

**Development model: AI-augmented engineering.**
Architecture, specs, UX, prioritization, code review, and testing are on the developer side. Code execution is delegated to Claude Code under detailed specs. Strategic decisions and architecture branches are worked through Claude.ai before any code is touched. Every change passes review and the test suite before reaching `main`.

This isn't vibe coding. The system has a defined architecture, ~880 tests, an ADHD-first UX layer, and a documented routing layer across Claude Sonnet, Haiku, and Whisper. AI is the executor; design and direction are human.

---

## What it does

### ☀️ Nexus — personal assistant
Telegram-first daily companion: tasks with streaks and recurring patterns, expense and income tracking with budget limits and overflow logic, shopping/checklist/inventory lists with a shared schema, free-form notes, persistent memory, ADHD-tuned reactions and confirmations, photo receipts (Vision), voice notes (Whisper). Multi-user.

Bot: [@nexus_kailark_bot](https://t.me/nexus_kailark_bot)

### 🌒 Arcana — esoteric-practice CRM
Client database with relationship types and barter tracking, tarot session log across multiple decks (Rider-Waite, Dark Wood, Deviant Moon, Lenormand, atlas cards), ritual planning with sub-tasks, a personal grimoire database, and a router that smart-redirects everyday requests back to Nexus.

Bot: [@arcana_kailark_bot](https://t.me/arcana_kailark_bot)

### 📱 Mini App
A single React + Vite + FastAPI app served as a Telegram WebApp. Six tabs for Nexus (Today, Tasks, Finance, Lists, Notes, Memory) and five for Arcana (Today, Sessions, Clients, Works, Grimoire). Glass-card design, sage-green / deep-blue palette, Lora serif, designed for thumb-reach on mobile.

---

## Architecture highlights

- **Dual-bot intent routing** — every user message passes through a Haiku-based classifier with few-shot examples, then a deterministic intent splitter, before dispatching to a per-domain handler.
- **Model routing for cost** — Haiku for routine classification and parsing, Sonnet only for high-value tasks (budget analyst, ADHD coaching, Vision on receipts, tarot interpretation, session summaries). Regression-protected with `tests/test_models_audit.py`.
- **State strictly in SQLite** — every pending dialog state is persisted, never in-memory dicts. Survives restarts.
- **Notion as source of truth** — 12 databases across three workspaces. All schema operations go through `match_select` to handle emoji-prefixed options.
- **ADHD-first UX** — color-coded button states, soft confirmations, reaction-based feedback, reply-as-augmentation pattern, no nested menus deeper than two levels.
- **Photo and voice** — Vision-based receipt parsing, Whisper-based voice notes via OpenAI.

`ARCHITECTURE.md` with diagrams is in the backlog.

---

## Tech stack

**Backend** — Python 3.9, aiogram 3.x, APScheduler, SQLite, Notion API.
**AI** — Anthropic Claude (Haiku + Sonnet, model-routed), OpenAI Whisper, Anthropic Vision.
**Mini App** — React + Vite + FastAPI + Cloudinary.
**Infra (current)** — local Mac development, planned VPS migration (Hetzner).
**Tooling** — Claude Code, GitHub Issues for backlog and bug-tracking, manual review.

---

## Status

- **Nexus** — production. Feature-complete on the v9 roadmap.
- **Arcana** — production. Feature-complete on the v8 roadmap (CRM, sessions, rituals, works with sub-tasks, grimoire).
- **Mini App** — active development.
- **Tests** — ~880 passing across unit, integration, and regression suites.
- **Backlog and bug-tracking** — [GitHub Issues](https://github.com/dontkaiad/nexus-arcana/issues).

---

## Repository layout

```
core/      shared infrastructure: Notion client, Claude client, classifier, memory, vision
nexus/     Nexus bot handlers, scheduler, ADHD layer
arcana/    Arcana bot handlers, sessions, grimoire
miniapp/   React frontend + FastAPI backend
tests/     pytest suite — unit + integration + regression + models-audit
docs/      technical specs (Notion schema, Lists, etc.)
```

---

## Workflow

1. Work begins as a **GitHub Issue** — bug or feature, with acceptance criteria.
2. Architectural tradeoffs are discussed in **Claude.ai** before code changes.
3. Implementation is delegated to **Claude Code** under a prompt referencing the issue.
4. Each change is reviewed against the spec, tests are run, and the issue is closed via `fixes #N` in the commit.
5. `main` is the working branch.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full cycle.

---

## Contact

- Telegram: [@hey_lark](https://t.me/hey_lark)
- GitHub: [@dontkaiad](https://github.com/dontkaiad)
- Bots: [@nexus_kailark_bot](https://t.me/nexus_kailark_bot) · [@arcana_kailark_bot](https://t.me/arcana_kailark_bot)

License: [MIT](LICENSE).
