# ☀️ Nexus & 🌒 Arcana

> Two Telegram LLM bots + a Mini App that I designed, built, and use every day.
> **☀️ Nexus** is my personal ADHD assistant — tasks, finances, budgets, nudges.
> **🌒 Arcana** is the CRM for a small esoteric practice — clients, tarot sessions, rituals.
> Production, 24/7, one developer — me.

![Python](https://img.shields.io/badge/Python-3.9-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Vite-61DAFB?logo=react&logoColor=white)
![tests](https://img.shields.io/badge/tests-passing-brightgreen)

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

## What it does

- **Natural-language in, structured out** — type, send a voice note, or snap a photo; it becomes a task, an expense, a client, a tarot session.
- **Voice & vision** — voice notes transcribed (Whisper), receipts and tarot spreads read by Claude Vision.
- **Cost-aware LLM routing** — Haiku for routine parsing/classification, Sonnet only where reasoning or empathy earns its price.
- **Reply-to-edit** — reply to any confirmation message to amend the record; the bot re-parses just the change.
- **ADHD-first UX** — near-zero friction to capture, a Mini App to review on a real screen, inline confirmations everywhere.

## Engineering highlights

- **PostgreSQL + ports-and-adapters**, migrated off Notion by a verified direct cutover — handlers never see SQL, the domain never sees the backend → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Cost-aware model routing** (Haiku default, Sonnet targeted), regression-guarded by a test → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **10 domain specs that document the real code** (conforms-to hashes, no aspirational models) → [docs/specs/](docs/specs/)
- **ADRs for the decisions that mattered** — identity hub, FK-vs-junction, direct cutover → [docs/CASES/](docs/CASES/)

**Full architecture & reasoning → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Stack

Python 3.9 · aiogram 3.13 · FastAPI · React/Vite · PostgreSQL 16 · SQLAlchemy Core + Alembic · Claude API (Haiku/Sonnet) · Claude Vision · OpenAI Whisper · Cloudinary · Docker · Vultr VPS

## Status

A personal production system — single user (me), running 24/7, and actively developed as
my AI/LLM engineering proving ground. Built AI-augmented: I own the architecture, specs,
UX, and review; execution is delegated under detailed specs, and every change ships with a
green test suite.

Bots: [@nexus_kailark_bot](https://t.me/nexus_kailark_bot) · [@arcana_kailark_bot](https://t.me/arcana_kailark_bot) — by Kai Lark ([@hey_lark](https://t.me/hey_lark))
